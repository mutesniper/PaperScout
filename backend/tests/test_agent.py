import asyncio
import time
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.agent_budget import Budget, BudgetExceeded, current_budget, reserve
from app.schemas import SearchResult, SearchRequest, Plan, SourceResult
from test_comparison import prepare
from test_core import paper
from test_qa import document


def wait(client, identifier):
    for _ in range(300):
        result = client.get('/api/agent/runs/' + identifier).json()
        if result['status'] not in ('queued', 'running'):
            return result
        time.sleep(.01)
    pytest.fail('job did not finish')


def start(client, ids=None):
    response = client.post('/api/agent/runs', json={'goal': '比较这两篇论文的方法', 'reading_ids': ids or []})
    assert response.status_code == 202, response.text
    return response.json()['id']


def test_real_graph_comparison_and_persistent_history(tmp_path):
    path = tmp_path / 'db'
    with TestClient(create_app(path)) as client:
        prepare(client)
        client.app.state.model.agent_decide = AsyncMock(side_effect=[
            {'tool': 'compare', 'reason': '对比所选论文的方法', 'args': {'reading_ids': ['paper', 'other'], 'dimensions': ['method']}},
            {'tool': 'finish', 'reason': '已完成对比'}])
        identifier = start(client, ['paper', 'other'])
        result = wait(client, identifier)
        assert result['status'] == 'completed'
        assert result['mode'] == 'single'
        assert result['steps'] == 2 and len(result['comparison']['cells']) == 2
        assert client.app.state.model.compare_papers.await_count == 1
        assert result['comparison']['cells'][0]['evidence'][0]['reading_id'] == 'paper'
    with TestClient(create_app(path)) as client:
        assert client.get('/api/agent/runs/' + identifier).json() == result
        assert client.get('/api/agent/runs').json()[0] == result


def test_search_download_failure_then_replan_finish(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        search = SearchResult(id='search1', created_at='2026-10-24', request=SearchRequest(plan=Plan(queries=['tools'])),
            papers=[paper(fulltext_url='https://example.org/a.pdf', access='open_fulltext')],
            sources=[SourceResult(source='arxiv', status='ok', count=1)])
        client.app.state.collect_search = AsyncMock(return_value=search)
        download = AsyncMock(side_effect=HTTPException(502, '公开 PDF 获取失败，可上传。'))
        monkeypatch.setattr('app.tools.executor.import_paper', download)
        client.app.state.model.agent_decide = AsyncMock(side_effect=[
            {'tool': 'search', 'reason': '搜索工具调用论文', 'plan': {'queries': ['small models tools']}},
            {'tool': 'read', 'reason': '获取候选全文', 'paper_id': 'one'},
            {'tool': 'finish', 'reason': '公开全文获取失败，请上传 PDF'}])
        result = wait(client, start(client))
        assert result['status'] == 'partial'
        assert result['search_ids'] == ['search1'] and result['papers'][0]['paper_id'] == 'one'
        assert result['comparison'] is None
        assert '公开 PDF 获取失败' in result['events'][-3]['message']
        assert client.app.state.model.agent_decide.call_args.args[0]['events'][-2]['status'] == 'error'
        assert download.await_count == 1


def test_foreign_ids_and_repeated_tools_are_not_executed(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        action = {'tool': 'compare', 'reason': '比较', 'args': {'reading_ids': ['paper', 'other'], 'dimensions': ['method']}}
        client.app.state.model.agent_decide = AsyncMock(return_value=action)
        result = wait(client, start(client, ['paper']))
        assert result['status'] == 'limited' and result['steps'] == 3
        client.app.state.model.compare_papers.assert_not_awaited()
        assert result['comparison'] is None
        assert any('只能对比' in e['message'] for e in result['events'])


def test_failed_read_is_explicit_in_next_decision_and_repetition_stops(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        search = SearchResult(id='failure-search', created_at='2026-10-24', request=SearchRequest(plan=Plan(queries=['tools'])),
            papers=[paper(fulltext_url='https://arxiv.org/pdf/2510.03847', access='open_preprint')],
            sources=[SourceResult(source='arxiv', status='ok', count=1)])
        client.app.state.collect_search = AsyncMock(return_value=search)
        download = AsyncMock(side_effect=HTTPException(502, 'Fake-IP 网络阻止下载'))
        monkeypatch.setattr('app.tools.executor.import_paper', download)
        decisions = []
        async def choose(context, schema):
            decisions.append(context)
            if len(decisions) == 1:
                return {'tool': 'search', 'reason': '搜索', 'plan': {'queries': ['tools']}}
            if len(decisions) >= 3:
                assert context['read_outcomes']['one']['status'] == 'failed'
                assert 'Fake-IP' in context['read_outcomes']['one']['reason']
                assert any('one' in action for action in context['attempted_actions'])
            return {'tool': 'read', 'reason': '获取全文', 'paper_id': 'one'}
        client.app.state.model.agent_decide = AsyncMock(side_effect=choose)
        result = wait(client, start(client))
        assert result['status'] == 'limited' and result['steps'] == 4
        assert download.await_count == 1


def test_cancel_and_concurrency_limit(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        async def slow(*args):
            await asyncio.sleep(30)
        client.app.state.model.agent_decide = AsyncMock(side_effect=slow)
        identifier = start(client)
        assert client.post('/api/agent/runs', json={'goal': 'another task'}).status_code == 409
        response = client.post('/api/agent/runs/' + identifier + '/cancel')
        assert response.status_code == 200 and response.json()['status'] == 'cancelled'
        assert client.get('/api/agent/runs/' + identifier).json()['status'] == 'cancelled'
        assert client.get('/api/agent/runs/missing').status_code == 404


def test_invalid_decision_timeout_and_restart(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        client.app.state.model.agent_decide = AsyncMock(return_value={'tool': 'shell', 'command': 'anything'})
        result = wait(client, start(client))
        assert result['status'] == 'failed'
        async def slow(*args):
            await asyncio.sleep(30)
        monkeypatch.setattr('app.agent.TOTAL_TIMEOUT', .05)
        client.app.state.model.agent_decide = AsyncMock(side_effect=slow)
        assert wait(client, start(client))['status'] == 'limited'
        result.update(status='running')
        client.app.state.store.save_agent_run(result)
    with TestClient(create_app(tmp_path / 'db')) as client:
        assert client.get('/api/agent/runs/' + result['id']).json()['status'] == 'interrupted'


def test_model_budget_and_task_budget_stop(tmp_path):
    budget = Budget()
    token = current_budget.set(budget)
    try:
        for _ in range(12): reserve('generations', 10)
        with pytest.raises(BudgetExceeded): reserve('generations', 10)
        assert budget.generations == 12
        with pytest.raises(BudgetExceeded): reserve('embeddings', 300_001)
        assert budget.embeddings == 0
    finally:
        current_budget.reset(token)
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        client.app.state.model.agent_decide = AsyncMock(side_effect=BudgetExceeded('调用已达上限'))
        assert wait(client, start(client))['status'] == 'limited'


def test_input_validation(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        for payload in ({'goal': ' '}, {'goal': 'x' * 3001}, {'goal': 'test', 'reading_ids': ['absent']},
                        {'goal': 'test', 'reading_ids': list('abcdef')}):
            assert client.post('/api/agent/runs', json=payload).status_code == 422
        client.app.state.model.api_key = ''
        assert client.post('/api/agent/runs', json={'goal': 'test'}).status_code == 503


def test_search_read_then_evidence_answer(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        search = SearchResult(id='search2', created_at='2026-10-24', request=SearchRequest(plan=Plan(queries=['tools'])),
            papers=[paper(fulltext_url='https://example.org/a.pdf', access='open_fulltext')],
            sources=[SourceResult(source='arxiv', status='ok', count=1)])
        client.app.state.collect_search = AsyncMock(return_value=search)
        async def imported(body, request):
            assert body.paper_id == 'one' and body.record_id == 'search2'
            doc = document('downloaded')
            request.app.state.store.save_reading(doc)
            return doc
        monkeypatch.setattr('app.tools.executor.import_paper', imported)
        client.app.state.model.agent_decide = AsyncMock(side_effect=[
            {'tool': 'search', 'reason': '搜索', 'plan': {'queries': ['tool calling']}},
            {'tool': 'read', 'reason': '阅读', 'paper_id': 'one'},
            {'tool': 'answer', 'reason': '提取局限', 'reading_id': 'downloaded', 'question': '有哪些局限？'},
            {'tool': 'finish', 'reason': '已整理证据'}])
        result = wait(client, start(client))
        assert result['status'] == 'completed' and result['readings'][0]['id'] == 'downloaded'
        assert result['answers'][0]['claims'][0]['evidence'][0]['page'] == 2


def test_model_decision_request_has_budget_and_no_extra_retries(monkeypatch):
    import json
    import httpx
    from app.models import CloudModel
    from app.tools.schemas import ACTION
    async def check():
        budget = Budget()
        token = current_budget.set(budget)
        calls = []
        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            assert body['max_tokens'] <= 2048
            assert body['enable_thinking'] is False
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'content': '{"tool":"finish","reason":"没有可用资料"}'}}]})
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                model = CloudModel(client, 'https://test.cn-beijing.maas.aliyuncs.com/v1', 'fake', 'deepseek-v4.1-flash')
                result = await model.agent_decide({'goal': 'find papers'}, ACTION.json_schema())
                assert ACTION.validate_python(result).tool == 'finish'
                assert budget.generations == 1 and budget.input_chars > 0 and len(calls) == 1
        finally:
            current_budget.reset(token)
    asyncio.run(check())


@pytest.mark.parametrize('count, public, failed, fallback', [
    (5, True, False, False), (4, True, False, True), (0, False, True, True),
    (6, False, False, True), (5, True, True, True)])
def test_arxiv_first_search_policy(count, public, failed, fallback):
    from types import SimpleNamespace
    from app.tools.search import search_arxiv_first
    async def check():
        calls, events = [], []
        async def collect(body):
            calls.append(body)
            source = body.sources[0]
            rows = [paper(id=f'p{i}', title=f'Unique research paper on tool use number {i}',
                arxiv_id=f'2401.{i:05d}', sources=[source], source_ranks={source: i + 1},
                fulltext_url=f'https://arxiv.org/pdf/2401.{i:05d}' if public else None,
                access='open_preprint' if public else 'unknown') for i in range(count)] if source == 'arxiv' else [
                    paper(id='s2-duplicate', arxiv_id='2401.00000', sources=[source], source_ranks={source: 1})]
            return SearchResult(id='first' if source == 'arxiv' else 'second', created_at='2026-10-24',
                request=body, papers=rows, sources=[SourceResult(source=source,
                    status='error' if failed and source == 'arxiv' else 'ok', count=len(rows))])
        app = SimpleNamespace(state=SimpleNamespace(collect_search=collect))
        plan = Plan(queries=['small model tools', 'extra query'], year_from=2023, year_to=2025)
        result = await search_arxiv_first(app, plan, lambda *args: events.append(args))
        assert [call.sources for call in calls] == ([['arxiv'], ['semantic_scholar']] if fallback else [['arxiv']])
        assert all(call.plan.queries == ['small model tools'] for call in calls)
        assert all(call.plan.year_from == 2023 and call.plan.year_to == 2025 for call in calls)
        assert plan.queries == ['small model tools', 'extra query']
        assert result.request.limit == 10 and len(result.papers) <= 10
        if fallback and count:
            assert result.candidate_count == count  # Cross-source duplicate merged.
            assert set(result.papers[0].source_ranks) == {'arxiv', 'semantic_scholar'}
        if not fallback:
            assert any('跳过 Semantic Scholar' in event[2] for event in events)
    asyncio.run(check())


def test_semantic_failure_preserves_arxiv_results():
    from types import SimpleNamespace
    from app.tools.search import search_arxiv_first
    async def check():
        async def collect(body):
            source = body.sources[0]
            return SearchResult(id=source, created_at='2026-10-24', request=body,
                papers=[paper(id='kept', sources=['arxiv'], source_ranks={'arxiv': 1})] if source == 'arxiv' else [],
                sources=[SourceResult(source=source, status='ok' if source == 'arxiv' else 'error', count=1 if source == 'arxiv' else 0, message='来源限流' if source != 'arxiv' else '')])
        result = await search_arxiv_first(SimpleNamespace(state=SimpleNamespace(collect_search=collect)),
            Plan(queries=['tools']), lambda *args: None)
        assert [p.id for p in result.papers] == ['kept']
        assert result.sources[1].status == 'error'
    asyncio.run(check())


@pytest.mark.parametrize('legacy_tool', ['read', 'download_pdf'])
def test_existing_pdf_download_is_corrected_then_answered(tmp_path, monkeypatch, legacy_tool):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        download = AsyncMock(side_effect=AssertionError('Existing PDF must not be downloaded'))
        monkeypatch.setattr('app.tools.executor.import_paper', download)
        contexts = []
        async def choose(context, schema):
            contexts.append(context)
            assert 'download_pdf' not in schema['discriminator']['mapping']
            assert 'ReadAction' not in schema['$defs']
            assert 'compare' not in schema['discriminator']['mapping']
            assert context['available_pdfs'][0] == {
                'reading_id': 'paper', 'title': 'paper', 'status': 'ready_for_analysis'}
            if len(contexts) == 1:
                return {'tool': legacy_tool, 'paper_id': 'paper', 'reason': '误把分析当下载'}
            if len(contexts) == 2:
                assert context['events'][-2]['status'] == 'corrected'
                assert '不是文件读取失败' in context['events'][-2]['message']
                assert 'paper' not in context['read_outcomes']
                assert context['remaining']['download_pdf'] == 5
                return {'tool': 'answer', 'reading_id': 'paper', 'question': '有哪些局限？', 'reason': '直接分析已有论文'}
            return {'tool': 'finish', 'reason': '已完成证据问答'}
        client.app.state.model.agent_decide = AsyncMock(side_effect=choose)
        result = wait(client, start(client, ['paper']))
        assert result['status'] == 'completed'
        assert result['answers'][0]['claims'][0]['evidence'][0]['page'] == 2
        download.assert_not_awaited()


def test_repeated_existing_pdf_download_corrections_are_bounded(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        download = AsyncMock()
        monkeypatch.setattr('app.tools.executor.import_paper', download)
        client.app.state.model.agent_decide = AsyncMock(return_value={
            'tool': 'download_pdf', 'paper_id': 'paper', 'reason': '读取'})
        result = wait(client, start(client, ['paper']))
        assert result['status'] == 'limited' and result['steps'] == 2
        download.assert_not_awaited()


def test_download_only_advertised_for_unattempted_public_candidates(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        search = SearchResult(id='state-search', created_at='2026-10-26', request=SearchRequest(plan=Plan(queries=['tools'])),
            papers=[paper(fulltext_url='https://example.org/a.pdf', access='open_fulltext')],
            sources=[SourceResult(source='arxiv', status='ok', count=1)])
        client.app.state.collect_search = AsyncMock(return_value=search)
        download = AsyncMock(side_effect=HTTPException(502, '下载失败'))
        monkeypatch.setattr('app.tools.executor.import_paper', download)
        calls = []
        async def choose(context, schema):
            allowed = schema['discriminator']['mapping']
            calls.append(context)
            assert 'read' not in allowed
            if len(calls) == 1:
                assert set(allowed) == {'search', 'finish'}
                return {'tool': 'search', 'reason': '搜索', 'plan': {'queries': ['tools']}}
            if len(calls) == 2:
                assert 'download_pdf' in allowed
                assert context['downloadable_candidate_ids'] == ['one']
                return {'tool': 'download_pdf', 'paper_id': 'one', 'reason': '下载候选'}
            assert 'download_pdf' not in allowed
            assert context['downloadable_candidate_ids'] == []
            assert context['read_outcomes']['one']['status'] == 'failed'
            return {'tool': 'finish', 'reason': '网络阻碍'}
        client.app.state.model.agent_decide = AsyncMock(side_effect=choose)
        assert wait(client, start(client))['status'] == 'partial'
        download.assert_awaited_once()

@pytest.mark.parametrize('mode', ['single', 'multi'])
def test_title_empty_search_simplifies_and_skips_secondary(tmp_path, mode):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        calls = []
        async def collect(body):
            calls.append(body)
            assert body.sources == ['arxiv']
            rows = [] if len(calls) == 1 else [paper(title='TinyAgent: Function Calling at the Edge',
                fulltext_url='https://arxiv.org/pdf/2409.00608', access='open_preprint')]
            return SearchResult(id='title-search', created_at='2026-10-27', request=body,
                papers=rows, sources=[SourceResult(source='arxiv', status='ok', count=len(rows))])
        client.app.state.collect_search = collect
        search = {'tool': 'search', 'reason': '查找指定论文',
                  'plan': {'queries': ['TinyAgent: Function Calling at the Edge']}}
        finish = {'tool': 'finish', 'reason': '已找到候选'}
        client.app.state.model.agent_decide = AsyncMock(side_effect=[search, finish])
        client.app.state.model.agent_role_decide = AsyncMock(side_effect=[
            {'tool': 'delegate', 'role': 'retrieval', 'task': '查找指定论文', 'reason': '搜索'}, search, finish])
        identifier = client.post('/api/agent/runs', json={'goal': '搜索 TinyAgent', 'mode': mode}).json()['id']
        result = wait(client, identifier)
        assert result['status'] == 'completed'
        assert len(calls) == 2
        assert calls[1].plan.queries == ['TinyAgent Function Calling Edge']
        assert result['papers'][0]['title'] == 'TinyAgent: Function Calling at the Edge'
        assert any('去掉常见虚词' in e['message'] for e in result['events'])


def test_arxiv_error_does_not_trigger_simplification():
    from types import SimpleNamespace
    from app.tools.search import search_arxiv_first
    calls = []
    async def collect(body):
        calls.append(body)
        return SearchResult(id='failure', created_at='2026-10-27', request=body, papers=[],
            sources=[SourceResult(source=body.sources[0], status='error', count=0, message='来源限流')])
    asyncio.run(search_arxiv_first(SimpleNamespace(state=SimpleNamespace(collect_search=collect)),
        Plan(queries=['TinyAgent at the Edge']), lambda *args: None))
    assert [c.sources for c in calls] == [['arxiv'], ['semantic_scholar']]
