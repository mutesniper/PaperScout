import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.agent_budget import reserve
from app.schemas import SearchResult, SearchRequest, Plan, SourceResult
from test_agent import wait
from test_comparison import prepare
from test_core import paper
from test_qa import document


def delegate(role, task='执行下一步研究任务'):
    return {'tool': 'delegate', 'role': role, 'task': task, 'reason': '按目标分配任务'}


def launch(client, ids=None):
    response = client.post('/api/agent/runs', json={'goal': '研究论文的方法', 'mode': 'multi', 'reading_ids': ids or []})
    assert response.status_code == 202, response.text
    assert response.json()['mode'] == 'multi'
    return response.json()['id']


def test_three_roles_search_read_answer_and_persistence(tmp_path, monkeypatch):
    path = tmp_path / 'db'
    with TestClient(create_app(path)) as client:
        prepare(client)
        search = SearchResult(id='search', created_at='2026-10-26', request=SearchRequest(plan=Plan(queries=['tools'])),
            papers=[paper(fulltext_url='https://example.org/a.pdf', access='open_fulltext')],
            sources=[SourceResult(source='arxiv', status='ok', count=1)])
        client.app.state.collect_search = AsyncMock(return_value=search)
        async def imported(body, request):
            doc = document('downloaded')
            request.app.state.store.save_reading(doc)
            return doc
        monkeypatch.setattr('app.tools.executor.import_paper', imported)
        choices = iter([
            ('coordinator', delegate('retrieval')),
            ('retrieval', {'tool': 'search', 'reason': '查找论文', 'plan': {'queries': ['tools', 'ignored']}}),
            ('coordinator', delegate('retrieval')),
            ('retrieval', {'tool': 'read', 'paper_id': 'one', 'reason': '获取论文'}),
            ('coordinator', delegate('analysis')),
            ('analysis', {'tool': 'answer', 'reading_id': 'downloaded', 'question': '有哪些局限？', 'reason': '阅读证据'}),
            ('coordinator', {'tool': 'finish', 'reason': '已获取证据'})])
        contexts = []
        async def choose(role, context, schema):
            expected, output = next(choices)
            assert role == expected
            contexts.append(context)
            reserve('generations', 100)
            return output
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=choose)
        client.app.state.model.agent_decide = AsyncMock(side_effect=AssertionError('single mode called'))
        result = wait(client, launch(client))
        assert result['status'] == 'completed', result
        assert result['mode'] == 'multi' and result['steps'] == 7
        assert result['usage']['generations'] == 7
        assert result['answers'][0]['claims'][0]['evidence'][0]['page'] == 2
        assert contexts[-1]['answer_results'][0]['supported']
        assert contexts[4]['read_outcomes']['one']['reading_id'] == 'downloaded'
        assert {e['role'] for e in result['events']} == {'coordinator', 'retrieval', 'analysis'}
        assert all(call.args[0].plan.queries == ['tools'] for call in client.app.state.collect_search.call_args_list)
    with TestClient(create_app(path)) as client:
        assert client.get('/api/agent/runs').json()[0] == result


def test_selected_pdfs_skip_retrieval_and_comparison_keeps_evidence(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        client.app.state.collect_search = AsyncMock(side_effect=AssertionError('unnecessary search'))
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=[delegate('analysis'),
            {'tool': 'compare', 'reason': '比较方法', 'args': {'reading_ids': ['paper', 'other'], 'dimensions': ['method']}},
            {'tool': 'finish', 'reason': '已生成对比'}])
        result = wait(client, launch(client, ['paper', 'other']))
        assert result['status'] == 'completed'
        assert len(result['comparison']['cells']) == 2
        assert result['comparison']['cells'][0]['evidence'][0]['reading_id'] == 'paper'
        client.app.state.collect_search.assert_not_awaited()


@pytest.mark.parametrize('role,action', [
    ('analysis', {'tool': 'search', 'reason': '越权', 'plan': {'queries': ['tools']}}),
    ('retrieval', {'tool': 'answer', 'reason': '越权', 'reading_id': 'paper', 'question': '什么方法？'})])
def test_role_scopes_are_enforced_before_tool_execution(tmp_path, role, action):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        client.app.state.collect_search = AsyncMock()
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=[delegate(role), action])
        result = wait(client, launch(client, ['paper']))
        assert result['status'] == 'failed'
        assert not result['answers'] and not result['search_ids']
        client.app.state.collect_search.assert_not_awaited()
        client.app.state.model.answer_question.assert_not_awaited()


def test_cross_task_ids_repeated_tools_and_report_loops_stop(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        action = {'tool': 'compare', 'reason': '比较', 'args': {'reading_ids': ['paper', 'other'], 'dimensions': ['method']}}
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=[delegate('analysis'), action] * 3)
        result = wait(client, launch(client, ['paper']))
        assert result['status'] == 'limited'
        assert any('只能对比' in e['message'] for e in result['events'])
        client.app.state.model.compare_papers.assert_not_awaited()
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=[delegate('analysis'),
            {'tool': 'report', 'reason': '缺少可读 PDF'}] * 2)
        result = wait(client, launch(client))
        assert result['status'] == 'limited' and result['steps'] == 4


def test_download_error_handed_to_coordinator_and_budget_shared(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        search = SearchResult(id='s', created_at='2026-10-26', request=SearchRequest(plan=Plan(queries=['tools'])),
            papers=[paper(fulltext_url='https://example.org/a.pdf', access='open_fulltext')],
            sources=[SourceResult(source='arxiv', status='ok', count=1)])
        client.app.state.collect_search = AsyncMock(return_value=search)
        monkeypatch.setattr('app.tools.executor.import_paper', AsyncMock(side_effect=HTTPException(502, 'Fake-IP')))
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=[delegate('retrieval'),
            {'tool': 'search', 'reason': '搜索', 'plan': {'queries': ['tools']}}, delegate('retrieval'),
            {'tool': 'read', 'reason': '下载', 'paper_id': 'one'}, {'tool': 'finish', 'reason': '下载受阻'}])
        result = wait(client, launch(client))
        assert result['status'] == 'partial'
        assert client.app.state.model.agent_role_decide.call_args.args[1]['read_outcomes']['one']['reason'] == 'Fake-IP'
        async def consume(role, context, schema):
            reserve('generations', 1)
            if role == 'coordinator':
                return delegate('analysis')
            for _ in range(12):
                reserve('generations', 1)
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=consume)
        result = wait(client, launch(client))
        assert result['status'] == 'limited' and result['usage']['generations'] == 12


def test_multi_cancel_timeout_and_validation(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        async def choose(role, *args):
            if role == 'coordinator':
                return delegate('analysis')
            await asyncio.sleep(30)
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=choose)
        identifier = launch(client, ['paper'])
        assert client.post('/api/agent/runs', json={'goal': 'another', 'mode': 'single'}).status_code == 409
        result = client.post(f'/api/agent/runs/{identifier}/cancel').json()
        assert result['status'] == 'cancelled' and result['mode'] == 'multi'
        monkeypatch.setattr('app.agent.TOTAL_TIMEOUT', .05)
        assert wait(client, launch(client))['status'] == 'limited'
        assert client.post('/api/agent/runs', json={'goal': 'test', 'mode': 'unknown'}).status_code == 422


def test_total_decision_limit_stops_before_specialist_call(tmp_path, monkeypatch):
    monkeypatch.setattr('app.multi_agent.MAX_DECISIONS', 1)
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        client.app.state.model.agent_role_decide = AsyncMock(return_value=delegate('analysis'))
        result = wait(client, launch(client, ['paper']))
        assert result['status'] == 'limited' and result['steps'] == 1
        client.app.state.model.agent_role_decide.assert_awaited_once()


def test_role_prompts_and_budget_use_real_model_adapter():
    import json
    from app.models import CloudModel
    from app.multi_agent import COORDINATOR, RETRIEVAL, ANALYSIS
    from app.agent_budget import Budget, current_budget
    async def check():
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'content': '{"tool":"report","reason":"缺少资料"}'}}]})
        budget = Budget()
        token = current_budget.set(budget)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                model = CloudModel(client, 'https://test/v1', 'fake', 'test')
                for role, adapter in [('coordinator', COORDINATOR), ('retrieval', RETRIEVAL), ('analysis', ANALYSIS)]:
                    await model.agent_role_decide(role, {'goal': 'test'}, adapter.json_schema())
            assert len(bodies) == 3 and budget.generations == 3
            assert len({b['messages'][0]['content'] for b in bodies}) == 3
            assert all(b['max_tokens'] <= 2048 for b in bodies)
        finally:
            current_budget.reset(token)
    asyncio.run(check())
