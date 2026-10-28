from datetime import date
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.schemas import Paper, SearchRequest, SearchResult
from app.search.providers import deduplicate, parse_semantic
from app.search.ranking import source_candidates, rank_candidates

def paper(identifier, **fields):
    return Paper(id=identifier, title=identifier, sources=['arxiv'], url='https://example.org',
                 access='unknown', evidence='', **fields)

def test_rrf_merges_identity_and_counts_each_source_once():
    a = source_candidates([paper('a', doi='10/a'), paper('shared', doi='10/shared')], 'arxiv')
    b = source_candidates([paper('b', doi='10/b'), paper('alias', doi='10/shared')], 'semantic_scholar')
    ranked = rank_candidates(deduplicate(a + b), 'relevance')
    assert [p.id for p in ranked] == ['shared', 'a', 'b']
    assert ranked[0].source_ranks == {'arxiv': 2, 'semantic_scholar': 2}
    assert ranked[0].relevance_score == pytest.approx(2 / 62)
    duplicate = source_candidates([paper('a'), paper('a'), paper('b'), paper('a')], 'arxiv')
    assert duplicate[0].source_ranks == {'arxiv': 1}
    assert rank_candidates(duplicate, 'relevance')[0].relevance_score == pytest.approx(1 / 61)

def test_single_source_order():
    rows = source_candidates([paper('z'), paper('a')], 'arxiv')
    assert [p.id for p in rank_candidates(rows, 'relevance')] == ['z', 'a']

@pytest.mark.parametrize('mode,expected', [
    ('newest', ['late', 'early', 'year', 'old', 'unknown']),
    ('oldest', ['old', 'early', 'late', 'year', 'unknown']),
])
def test_dates_missing_and_year_precision(mode, expected):
    rows = [paper('unknown'), paper('year', year=2024), paper('old', year=2020),
            paper('late', publication_date=date(2024, 12, 1)), paper('early', publication_date=date(2024, 1, 1))]
    assert [p.id for p in rank_candidates(rows, mode)] == expected

def test_citations_zero_is_known_and_missing_is_last():
    rows = [paper('unknown'), paper('zero', citation_count=0), paper('high', citation_count=100)]
    assert [p.id for p in rank_candidates(rows, 'citations')] == ['high', 'zero', 'unknown']
    merged = deduplicate([paper('x', doi='10/x'), paper('y', doi='10/x', citation_count=0, citation_source='semantic_scholar')])
    assert merged[0].citation_count == 0 and merged[0].citation_source == 'semantic_scholar'

def test_provider_metadata():
    p = parse_semantic({'data': [{'paperId': 'a', 'title': 'a', 'publicationDate': '2024-05-12', 'citationCount': 0}]})[0]
    assert p.publication_date == date(2024, 5, 12) and p.citation_count == 0
    p = parse_semantic({'data': [{'paperId': 'a', 'title': 'a', 'publicationDate': 'bad'}]})[0]
    assert p.publication_date is None and p.citation_count is None

def test_api_ranks_before_truncation_and_reuses_raw_cache(tmp_path):
    with TestClient(create_app(tmp_path / 'ranking.db')) as client:
        fetch = AsyncMock(return_value=[paper('first', year=2020, citation_count=None),
                                        paper('second', year=2025, publication_date=date(2025, 2, 1), citation_count=10)])
        client.app.state.sources.fetch = fetch
        body = {'plan': {'queries': ['x']}, 'sources': ['arxiv'], 'candidate_limit': 20, 'limit': 1}
        relevance = client.post('/api/search', json=body).json()
        assert relevance['papers'][0]['id'] == 'first' and relevance['candidate_count'] == 2
        for mode in ('newest', 'citations'):
            result = client.post('/api/search', json={**body, 'sort': mode}).json()
            assert result['papers'][0]['id'] == 'second'
            assert result['sources'][0]['cached'] is True
            assert client.get('/api/history/' + result['id']).json() == result
        assert fetch.await_count == 1 and fetch.call_args.args[-1] == 100
        client.post('/api/search', json={**body, 'candidate_limit': 100})
        assert fetch.await_count == 1  # 忽略客户端提供的候选数量上限。

def test_old_history_and_invalid_inputs():
    old = SearchResult.model_validate({'id': 'old', 'created_at': '2024', 'request': {'plan': {'queries': ['x']}, 'limit': 10}, 'papers': [], 'sources': []})
    assert old.candidate_count is None and old.request.sort == 'relevance'
    for extra in ({'sort': 'downloads'}, {'limit': 0}):
        with pytest.raises(ValueError):
            SearchRequest.model_validate({'plan': {'queries': ['x']}, **extra})

def test_independent_queries_cap_each_source_even_if_provider_overreturns(tmp_path):
    with TestClient(create_app(tmp_path / 'combined.db')) as client:
        async def fetch(source, query, plan, limit):
            assert query in ('transformer', 'attention')
            assert limit == 100
            return [paper(f'{source}-{query}-{i}') for i in range(120)]
        client.app.state.sources.fetch = AsyncMock(side_effect=fetch)
        body = {'plan': {'queries': ['transformer', 'attention']}, 'limit': 100}
        result = client.post('/api/search', json=body).json()
        assert client.app.state.sources.fetch.await_count == 4
        assert [s['count'] for s in result['sources']] == [100, 100]
        assert result['candidate_count'] == 200 and len(result['papers']) == 100
        assert all(int(p['id'].rsplit('-', 1)[1]) < 50 for p in result['papers'])
        cached = client.post('/api/search', json=body).json()
        assert all(s['cached'] for s in cached['sources'])
        assert client.app.state.sources.fetch.await_count == 4


def test_query_failure_keeps_success_and_retries_only_failed_query(tmp_path):
    import httpx
    with TestClient(create_app(tmp_path / 'partial.db')) as client:
        async def fetch(source, query, plan, limit):
            if query == 'broken':
                raise httpx.ConnectError('offline')
            return [paper('shared', doi='10/shared'), paper(query)]
        mock = AsyncMock(side_effect=fetch)
        client.app.state.sources.fetch = mock
        body = {'plan': {'queries': ['first', 'broken', 'last']}, 'sources': ['arxiv']}
        result = client.post('/api/search', json=body).json()
        assert [p['id'] for p in result['papers']] == ['shared', 'first', 'last']
        assert result['sources'][0]['status'] == 'partial'
        assert '1/3' in result['sources'][0]['message']
        again = client.post('/api/search', json=body).json()
        assert len(again['papers']) == 3
        assert mock.await_count == 4
        assert mock.call_args.args[1] == 'broken'
