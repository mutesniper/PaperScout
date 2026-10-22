import asyncio
import json
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app.models import CloudModel
from app.schemas import GoalAnalysis
from app.model_output import ModelOutputError

def analysis_data():
    return dict(goal='让小模型使用工具', assumptions=['优先使用已有模型'], questions=['是否需要本地运行？'],
        knowledge=[dict(name='工具调用', importance='core', explanation='选择工具和参数',
            goal_connection='用于让小模型执行任务', plan=dict(queries=['small language models tool use'], year_from=None, year_to=None))],
        steps=['定义工具接口', '评测基础模型'], alternatives=['可选微调，先确认基础模型是否满足要求'])

def test_analysis_model_request_and_validation():
    async def check():
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(analysis_data())}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await CloudModel(client, 'https://example.test/v1', 'test-secret', 'test-model').analyze('想做工具助手', '只有8GB显存')
        assert result.knowledge[0].importance == 'core'
        user = json.loads(seen[0]['messages'][1]['content'])
        assert user == {'goal': '想做工具助手', 'clarification': '只有8GB显存'}
        assert 'NOT literature-verified' in seen[0]['messages'][0]['content']
    asyncio.run(check())
    for field, value in [('knowledge', []), ('steps', []), ('questions', ['q'] * 4)]:
        with pytest.raises(ValidationError):
            GoalAnalysis.model_validate({**analysis_data(), field: value})
    invalid = analysis_data()
    invalid['knowledge'][0]['plan']['queries'] = ['x', 'y']
    with pytest.raises(ValidationError):
        GoalAnalysis.model_validate(invalid)

def test_analysis_endpoint_and_context_history(tmp_path):
    with TestClient(create_app(tmp_path / 'test.db')) as client:
        client.app.state.model.analyze = AsyncMock(return_value=GoalAnalysis.model_validate(analysis_data()))
        response = client.post('/api/analyze', json={'text': '做一个助手', 'clarification': '本地运行'})
        assert response.status_code == 200
        assert response.json()['analysis']['goal'] == '让小模型使用工具'
        client.app.state.model.analyze.assert_awaited_once_with('做一个助手', '本地运行')
        client.app.state.sources.fetch = AsyncMock(return_value=[])
        context = {'goal': '做一个小模型助手', 'topic': '工具调用', 'reason': '用于执行任务'}
        result = client.post('/api/search', json={'plan': {'queries': ['small language models tool use']}, 'context': context}).json()
        assert client.get('/api/history/' + result['id']).json()['request']['context'] == context

@pytest.mark.parametrize('exception,status', [(RuntimeError('secret'),503), (ValueError('secret'),502), (httpx.ConnectError('secret'),502)])
def test_analysis_failure_is_safe(tmp_path, exception, status):
    with TestClient(create_app(tmp_path / 'test.db')) as client:
        client.app.state.model.analyze = AsyncMock(side_effect=exception)
        response = client.post('/api/analyze', json={'text': '目标描述'})
        assert response.status_code == status and 'secret' not in response.text
        assert client.post('/api/analyze', json={'text': '目标', 'clarification': 'x' * 3001}).status_code == 422

@pytest.mark.parametrize('style', ['fenced', 'blocks'])
def test_json_format_compatibility(style):
    async def check():
        text = json.dumps(analysis_data())
        content = '```json\n' + text + '\n```' if style == 'fenced' else [{'type': 'text', 'text': text}]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}))) as client:
            result = await CloudModel(client, 'https://example.test/v1', 'key', 'model').analyze('test goal')
        assert result.goal == analysis_data()['goal']
    asyncio.run(check())

@pytest.mark.parametrize('failure', ['schema', 'json', 'truncated', 'empty'])
def test_format_retry_recovers_and_logs_no_body(failure, caplog):
    async def check():
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls) == 2:
                return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(analysis_data())}}]})
            bad = analysis_data()
            bad['knowledge'][0]['importance'] = 'private-response-marker'
            content = {'schema': json.dumps(bad), 'json': 'private-response-marker',
                       'truncated': '{"private-response-marker":', 'empty': None}[failure]
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if failure == 'truncated' else 'stop',
                'message': {'content': content}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await CloudModel(client, 'https://example.test/v1', 'private-api-marker', 'model').analyze('private-goal-marker')
        assert result.goal == analysis_data()['goal'] and len(calls) == 2
        assert 'Regenerate' in calls[1]['messages'][0]['content']
    asyncio.run(check())
    assert 'private-response-marker' not in caplog.text and 'private-api-marker' not in caplog.text
    assert 'private-goal-marker' not in caplog.text

def test_retry_is_bounded_and_refusal_not_retried():
    async def check(refusal):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(200, json={'choices': [{'message': {'content': '', 'refusal': 'private' if refusal else None}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ModelOutputError) as error:
                await CloudModel(client, 'https://example.test/v1', 'key', 'model').analyze('test')
        assert error.value.code == ('refused' if refusal else 'empty')
        assert count == (1 if refusal else 2)
    asyncio.run(check(False))
    asyncio.run(check(True))

def test_query_schema_matches_single_query_constraint():
    schema = GoalAnalysis.model_json_schema()
    assert schema['$defs']['KnowledgePlan']['properties']['queries']['maxItems'] == 1

def test_output_error_is_specific_and_safe(tmp_path):
    with TestClient(create_app(tmp_path / 'test.db')) as client:
        client.app.state.model.analyze = AsyncMock(side_effect=ModelOutputError('truncated', 'private-marker'))
        response = client.post('/api/analyze', json={'text': '目标描述'})
        assert response.status_code == 502 and '截断' in response.json()['detail']
        assert 'private-marker' not in response.text

def test_analysis_budgets_allow_both_attempts(monkeypatch):
    from app.models import ANALYSIS_ATTEMPT_TIMEOUT, ANALYSIS_TOTAL_TIMEOUT
    assert ANALYSIS_TOTAL_TIMEOUT > 2 * ANALYSIS_ATTEMPT_TIMEOUT
    monkeypatch.setenv('LLM_ANALYSIS_MAX_TOKENS', '8192')
    async def check():
        budgets = []
        def handler(request):
            budgets.append(json.loads(request.content)['max_tokens'])
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if len(budgets) == 1 else 'stop',
                'message': {'content': json.dumps(analysis_data())}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await CloudModel(client, 'https://example.test/v1', 'key', 'model').analyze('test')
        assert budgets == [8192, 12288]
    asyncio.run(check())

@pytest.mark.parametrize('kind,status,message', [('timeout',504,'超时'), ('http',502,'限流')])
def test_timeout_and_http_are_distinguished(tmp_path, kind, status, message):
    request = httpx.Request('POST', 'https://example.test')
    exc = httpx.ReadTimeout('private-marker') if kind == 'timeout' else httpx.HTTPStatusError(
        'private-marker', request=request, response=httpx.Response(429, request=request))
    with TestClient(create_app(tmp_path / 'test.db')) as client:
        client.app.state.model.analyze = AsyncMock(side_effect=exc)
        response = client.post('/api/analyze', json={'text': '目标描述'})
        assert response.status_code == status and message in response.json()['detail']
        assert 'private-marker' not in response.text
