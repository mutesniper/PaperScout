import asyncio
import json
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app.models import CloudModel
from app.providers import deduplicate, parse_arxiv, parse_semantic, Sources
from app.schemas import Plan, Paper

def paper(**kwargs):
    fields = dict(id='one', title='A sufficiently long paper title for matching', authors=['A. Author'], year=2024,
                  sources=['arxiv'], url='https://arxiv.org/abs/1234', access='unknown', evidence='unknown')
    fields.update(kwargs)
    return Paper(**fields)

def test_plan_validation():
    for value in ({'queries': [' ']}, {'queries': ['x'], 'year_from': 2025, 'year_to': 2020}, {'queries': ['x'], 'invented': 1}):
        with pytest.raises(ValidationError): Plan.model_validate(value)
    assert Plan(queries=[' x ', 'x']).queries == ['x']

def test_dedupe_merge_and_conflicts():
    a = paper(doi='10.1/a')
    b = paper(id='two', doi='10.1/a', sources=['semantic_scholar'], fulltext_url='https://example.org/paper.pdf', access='open_fulltext')
    merged = deduplicate([a, b])
    assert len(merged) == 1 and len(merged[0].sources) == 2
    assert merged[0].access == 'open_fulltext'
    assert len(deduplicate([a, paper(id='three', doi='10.1/b')])) == 2
    assert len(deduplicate([paper(year=None), paper(id='two', year=None)])) == 2
    assert len(deduplicate([paper(), paper(id='two')])) == 1

def test_access_unknown_not_paywall():
    rows = parse_semantic({'data': [{'paperId': '1', 'title': 'Test', 'abstract': 'summary', 'openAccessPdf': None},
                                   {'paperId': '2', 'title': 'No abstract', 'openAccessPdf': {'url': 'javascript:alert(1)'}}]})
    assert rows[0].access == 'abstract_only'
    assert rows[1].access == 'unknown' and rows[1].fulltext_url is None

def test_arxiv_normalization():
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><id>http://arxiv.org/abs/2401.12345v2</id><title>A\n paper</title><published>2024-01-02T00:00:00Z</published><author><name>A</name></author><summary>Some evidence</summary><arxiv:doi>https://doi.org/10.1/ABC</arxiv:doi><link title="pdf" href="https://arxiv.org/pdf/2401.12345v2"/></entry></feed>'''
    p = parse_arxiv(xml)[0]
    assert p.arxiv_id == '2401.12345' and p.doi == '10.1/abc'
    assert p.title == 'A paper' and p.access == 'open_preprint'

def test_partial_failure_history_cache_and_years(tmp_path):
    with TestClient(create_app(tmp_path / 'db.sqlite')) as client:
        async def fetch(source, *args):
            if source == 'semantic_scholar':
                raise httpx.ReadTimeout('test timeout')
            return [paper(year=2024), paper(id='old', year=1999), paper(id='missing', year=None)]
        client.app.state.sources.fetch = AsyncMock(side_effect=fetch)
        body = {'plan': {'queries': ['tools'], 'year_from': 2020}}
        response = client.post('/api/search', json=body)
        assert response.status_code == 200
        result = response.json()
        assert len(result['papers']) == 1 and result['sources'][1]['status'] == 'error'
        assert client.get('/api/history').json()[0]['id'] == result['id']
        assert client.get('/api/history/' + result['id']).json() == result
        assert client.get('/api/history/not-found').status_code == 404
        second = client.post('/api/search', json=body).json()
        assert second['sources'][0]['cached'] is True

def test_all_failures_are_not_empty_success(tmp_path):
    with TestClient(create_app(tmp_path / 'db.sqlite')) as client:
        client.app.state.sources.fetch = AsyncMock(side_effect=httpx.ConnectError('offline'))
        result = client.post('/api/search', json={'plan': {'queries': ['tools']}}).json()
        assert result['papers'] == [] and all(s['status'] == 'error' for s in result['sources'])

def test_cloud_plan_validation():
    async def check():
        def handler(request):
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'queries': ['tool calling'], 'year_from': None, 'year_to': None})}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = CloudModel(client, 'https://example.test/v1', 'test-key', 'test-model')
            assert (await model.plan('tools')).queries == ['tool calling']
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': '{"queries": []}'}}]}))) as client:
            with pytest.raises(ValidationError):
                await CloudModel(client, 'https://example.test/v1', 'key', 'model').plan('tools')
    asyncio.run(check())

def test_plan_error_does_not_expose_secrets(tmp_path):
    with TestClient(create_app(tmp_path / 'db.sqlite')) as client:
        client.app.state.model.plan = AsyncMock(side_effect=httpx.ConnectError('secret-key'))
        response = client.post('/api/plan', json={'text': 'find papers'})
        assert response.status_code == 502 and 'secret-key' not in response.text

def test_source_query_and_year_encoding():
    async def check():
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>') if 'arxiv' in request.url.host else httpx.Response(200, json={'data': []})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = Sources(client)
            plan = Plan(queries=['tool use'], year_from=2020)
            await source.fetch('arxiv', 'tool use', plan, 5)
            await source.fetch('semantic_scholar', 'tool use', plan, 5)
        assert 'submittedDate:[202001010000' in seen[0].url.params['search_query']
        assert seen[1].url.params['year'] == '2020-'
    asyncio.run(check())

def test_storage_releases_file_handles(tmp_path):
    import sqlite3
    from app.storage import Store
    path = tmp_path / 'closed.sqlite'
    store = Store(path)
    store.cache('test', [])
    assert store.cached('test') == []
    assert store.history() == []
    with store.connect() as connection:
        assert connection.execute('SELECT 1').fetchone() == (1,)
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')

def test_malformed_source_is_a_validation_error():
    with pytest.raises(ValueError):
        parse_arxiv('<html>not xml')
