import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.agent.local_router import LocalRouter, RouterError, ADAPTER_SHA256, PROMPT_SHA256
from app.agent.router_contract import State, allowed_routes
from test_agent import wait
from test_comparison import prepare


def begin(client, ids=('paper','other'), **kwargs):
    response = client.post('/api/agent/runs',json={
        'goal':'先介绍第一篇，再比较两篇方法','reading_ids':list(ids),'router_mode':'local',**kwargs})
    assert response.status_code == 202, response.text
    return response.json()['id']


def test_local_compare_uses_locked_cloud_parameters_and_persists_mode(tmp_path):
    with TestClient(create_app(tmp_path/'db')) as client:
        prepare(client)
        app=client.app.state
        app.local_router.decide=AsyncMock(side_effect=['compare','finish'])
        app.model.agent_decide=AsyncMock()
        app.model.agent_parameters=AsyncMock(side_effect=[
            {'tool':'compare','reason':'对比方法','args':{'reading_ids':['paper','other'],'dimensions':['method']}},
            {'reason':'已对比','completed':True}])
        run=wait(client,begin(client))
        assert run['status']=='completed' and run['router_mode']=='local'
        app.model.agent_decide.assert_not_awaited()
        assert app.model.agent_parameters.call_args_list[0].args[2]['discriminator']['mapping'].keys()=={'compare'}
        assert run['usage']['local_routes']==2
        state=app.local_router.decide.call_args_list[1].args[0]
        assert state.remaining.compare==0
        assert any('对比' in text for text in state.results)


def test_clarification_resume_survives_restart_and_keeps_budget(tmp_path):
    path=tmp_path/'db'
    with TestClient(create_app(path)) as client:
        prepare(client)
        client.app.state.local_router.decide=AsyncMock(return_value='clarify')
        client.app.state.model.agent_parameters=AsyncMock(return_value={'question':'请选择两篇论文。'})
        identifier=begin(client)
        paused=wait(client,identifier)
        assert paused['status']=='needs_input' and paused['steps']==1
        # Persist a consumed allowance as if an earlier tool had run before the pause.
        paused['router_checkpoint']['counts']['search']=2
        paused['router_checkpoint']['attempted']=['prior-action']
        paused['usage']['generations']=3
        client.app.state.store.save_agent_run(paused)
    with TestClient(create_app(path)) as client:
        client.app.state.model.base_url='https://model.test'
        client.app.state.model.api_key='fake'
        client.app.state.model.name='test'
        from app.rag.retrieval import CloudEmbedding
        client.app.state.embedding=CloudEmbedding(None,'','','')
        client.app.state.local_router.decide=AsyncMock(return_value='finish')
        client.app.state.model.agent_parameters=AsyncMock(return_value={'reason':'资料不足，停止','completed':False})
        response=client.post(f'/api/agent/runs/{identifier}/reply',json={'text':'我选择第一篇与第二篇。'})
        assert response.status_code==202
        run=wait(client,identifier)
        assert run['status']=='partial' and run['steps']==2
        assert run['usage']['local_routes']==2 and run['usage']['generations']==3
        state=client.app.state.local_router.decide.call_args.args[0]
        assert state.remaining.search==0 and any('我选择' in r for r in state.results)
        assert run['router_checkpoint']['attempted']==['prior-action']
        assert client.post(f'/api/agent/runs/{identifier}/reply',json={'text':'again'}).status_code==409


@pytest.mark.parametrize('route,parameters',[
    ('compare',None),
    ('answer',{'tool':'search','reason':'换个工具','plan':{'queries':['tools']}}),
])
def test_unavailable_route_and_parameter_tool_switch_are_blocked(tmp_path,route,parameters):
    with TestClient(create_app(tmp_path/'db')) as client:
        prepare(client)
        client.app.state.local_router.decide=AsyncMock(return_value=route)
        client.app.state.model.agent_parameters=AsyncMock(return_value=parameters)
        client.app.state.model.agent_decide=AsyncMock()
        run=wait(client,begin(client,ids=('paper',)))
        assert run['status']=='failed'
        assert not run['answers'] and not run['search_ids']
        client.app.state.model.agent_decide.assert_not_awaited()
        if parameters is None:
            client.app.state.model.agent_parameters.assert_not_awaited()


def test_local_disconnect_cancel_and_mode_validation(tmp_path):
    with TestClient(create_app(tmp_path/'db')) as client:
        prepare(client)
        assert client.post('/api/agent/runs',json={'goal':'对比论文','mode':'multi','router_mode':'local'}).status_code==422
        assert client.post('/api/agent/runs',json={'goal':'x'*1501,'router_mode':'local'}).status_code==422
        client.app.state.local_router.decide=AsyncMock(side_effect=RouterError('本地服务断开'))
        client.app.state.model.agent_decide=AsyncMock()
        assert wait(client,begin(client))['message']=='本地服务断开'
        client.app.state.model.agent_decide.assert_not_awaited()
        client.app.state.local_router.decide=AsyncMock(return_value='clarify')
        client.app.state.model.agent_parameters=AsyncMock(return_value={'question':'选哪两篇？'})
        identifier=begin(client)
        assert wait(client,identifier)['status']=='needs_input'
        assert client.post(f'/api/agent/runs/{identifier}/cancel').json()['status']=='cancelled'
        async def slow(state):
            await asyncio.sleep(30)
        client.app.state.local_router.decide=slow
        identifier=begin(client)
        assert client.post(f'/api/agent/runs/{identifier}/cancel').json()['status']=='cancelled'


def test_http_identity_and_invalid_route_are_checked(monkeypatch):
    real_client=httpx.AsyncClient
    payload={'raw':'{"route":"search"}','adapter_sha256':ADAPTER_SHA256,'prompt_sha256':PROMPT_SHA256}
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json=payload))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=transport,**kwargs))
    assert asyncio.run(LocalRouter('http://localhost:8010').decide(State(goal='找论文')) )=='search'
    payload['raw']='{"route":"compare"}'
    with pytest.raises(RouterError):
        asyncio.run(LocalRouter('http://localhost:8010').decide(State(goal='找论文')))
    payload['adapter_sha256']='wrong-model'
    with pytest.raises(RouterError,match='版本不匹配'):
        asyncio.run(LocalRouter('http://localhost:8010').decide(State(goal='找论文')))


def test_runtime_contract_matches_trained_prompt():
    # Keep the backend independent of training imports in deployed containers.
    import importlib.util
    from pathlib import Path
    file=Path(__file__).resolve().parents[2]/'training/router/contract.py'
    spec=importlib.util.spec_from_file_location('trained_router_contract',file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SYSTEM_PROMPT.encode()==__import__('app.agent.router_contract',fromlist=['SYSTEM_PROMPT']).SYSTEM_PROMPT.encode()
    states=[State(goal='找论文'),State(goal='结束',remaining={'steps':0}),State(goal='比较',available_pdfs=[
        {'reading_id':'a','title':'A'}, {'reading_id':'b','title':'B'}])]
    for state in states:
        original=module.State.model_validate(state.model_dump())
        assert module.model_messages(original)==__import__('app.agent.router_contract',fromlist=['model_messages']).model_messages(state)
        assert module.allowed_routes(original)==allowed_routes(state)
