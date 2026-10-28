from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_qa import setup, document
from app.rag.comparison import comparison_chunks
from app.rag.retrieval import chunks_for, lexical_scores, MAX_EVIDENCE_CHARS
from app.rag.retrieval import CloudEmbedding
from app.papers.schemas import Section, Fragment


def test_goal_prefers_inline_abstract_over_author_research_interests():
    doc = document()
    doc.sections = [
        Section(id='s1', title='正文（未识别章节标题）', fragments=[Fragment(page=1,
            text='Paper title. Abstract—Existing agents keep calling tools after enough evidence is collected. '
                 'We present runtime control to stop redundant execution. ' + 'Details of the approach. ' * 60)]),
        Section(id='s2', title='REFERENCES', fragments=[Fragment(page=12,
            text='His research interests include research problem objective motivation contribution.')])]
    chunks = chunks_for(doc)
    scores = lexical_scores(chunks, 'research problem objective motivation contribution')
    ranked = sorted(chunks, key=lambda c: scores[chunks.index(c)], reverse=True)[:6]
    assert ranked[0]['page'] == 12
    selected = comparison_chunks(doc, 'goal', ranked)
    assert len(selected) == 2
    assert 'Abstract' in selected[0]['text']
    assert all(c['page'] == 1 and len(c['text']) <= MAX_EVIDENCE_CHARS for c in selected)
    assert not any(c['page'] == 12 for c in comparison_chunks(doc, 'method', ranked))


def test_goal_uses_introduction_when_no_abstract_heading():
    doc = document()
    doc.sections[0].title = '1 Introduction'
    selected = comparison_chunks(doc, 'goal', [])
    assert selected[0]['section_id'] == 's1'


def test_goal_comparison_skips_fulltext_embedding(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        # Use separate long PDFs with explicit abstract sections.
        for identifier in ('survey1', 'survey2'):
            doc = document(identifier)
            doc.sections[0].title = 'Abstract'
            doc.sections[1].fragments[0].text = 'Long survey material. ' * 10000
            client.app.state.store.save_reading(doc)
        embedding = CloudEmbedding(None, 'https://embedding.test', 'fake', 'embedding-model')
        embedding.embed = AsyncMock(side_effect=AssertionError('goal evidence must not index full text'))
        client.app.state.embedding = embedding
        result = client.post('/api/comparisons', json={'reading_ids': ['survey1', 'survey2'], 'dimensions': ['goal']})
        assert result.status_code == 200, result.text
        assert result.json()['retrieval'] == 'structural'
        assert len(result.json()['cells']) == 2
        embedding.embed.assert_not_awaited()
        client.app.state.model.compare_papers.assert_awaited_once()


def prepare(client):
    setup(client)
    client.app.state.store.save_reading(document('other'))
    async def compare(material):
        return {'cells': [{'reading_id': item['reading_id'], 'dimension': item['dimension'],
            'supported': True, 'text': '采用语言模型选择工具。',
            'evidence_ids': [item['excerpts'][0]['id']]} for item in material]}
    client.app.state.model.compare_papers = AsyncMock(side_effect=compare)


def test_comparison_citations_and_persistence(tmp_path):
    path = tmp_path / 'db'
    with TestClient(create_app(path)) as client:
        prepare(client)
        response = client.post('/api/comparisons', json={'reading_ids': ['paper', 'other'], 'dimensions': ['method', 'limitations']})
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result['cells']) == 4
        for cell in result['cells']:
            assert cell['evidence'][0]['reading_id'] == cell['reading_id']
            assert cell['evidence'][0]['page'] in (1, 2)
        client.app.state.model.compare_papers.assert_awaited_once()
        material = client.app.state.model.compare_papers.call_args.args[0]
        assert all(len(item['excerpts']) <= 2 for item in material)
        assert all(len(chunk['text']) <= MAX_EVIDENCE_CHARS for item in material for chunk in item['excerpts'])
    with TestClient(create_app(path)) as client:
        assert client.get('/api/comparisons').json() == [result]


@pytest.mark.parametrize('fault', ['foreign', 'missing', 'duplicate', 'unsupported_with_citation'])
def test_invalid_cells_never_saved(tmp_path, fault):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        async def invalid(material):
            cells = [{'reading_id': item['reading_id'], 'dimension': item['dimension'], 'supported': True,
                'text': 'test', 'evidence_ids': [item['excerpts'][0]['id']]} for item in material]
            if fault == 'foreign': cells[0]['evidence_ids'] = cells[1]['evidence_ids']
            if fault == 'missing': cells.pop()
            if fault == 'duplicate': cells.append(cells[0])
            if fault == 'unsupported_with_citation': cells[0]['supported'] = False
            return {'cells': cells}
        client.app.state.model.compare_papers = AsyncMock(side_effect=invalid)
        assert client.post('/api/comparisons', json={'reading_ids': ['paper', 'other'], 'dimensions': ['method']}).status_code == 502
        assert client.get('/api/comparisons').json() == []


def test_selection_validation_and_service_failure(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        client.app.state.store.save_reading(document('abstract', 'abstract'))
        for ids, dimensions, status in [(['paper'], ['method'], 422), (['paper', 'paper'], ['method'], 422),
                (['paper', 'other'], [], 422), (['paper', 'other'], ['method', 'method'], 422),
                (['paper', 'other'], ['unknown'], 422), (['paper', 'absent'], ['method'], 404),
                (['paper', 'abstract'], ['method'], 422), (list('abcdef'), ['method'], 422)]:
            assert client.post('/api/comparisons', json={'reading_ids': ids, 'dimensions': dimensions}).status_code == status
        client.app.state.model.compare_papers.assert_not_awaited()
        client.app.state.model.compare_papers = AsyncMock(side_effect=httpx.ConnectError('private error'))
        response = client.post('/api/comparisons', json={'reading_ids': ['paper', 'other'], 'dimensions': ['method']})
        assert response.status_code == 502 and 'private error' not in response.text
        assert client.get('/api/comparisons').json() == []


def test_no_hits_and_model_insufficient_evidence(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        prepare(client)
        # Research-goal terms are absent in both fixtures: no generation needed.
        response = client.post('/api/comparisons', json={'reading_ids': ['paper', 'other'], 'dimensions': ['goal']})
        assert response.status_code == 200
        assert all(not cell['supported'] for cell in response.json()['cells'])
        client.app.state.model.compare_papers.assert_not_awaited()
        async def insufficient(material):
            return {'cells': [{'reading_id': item['reading_id'], 'dimension': item['dimension'],
                'supported': False, 'text': 'not enough', 'evidence_ids': []} for item in material]}
        client.app.state.model.compare_papers = AsyncMock(side_effect=insufficient)
        response = client.post('/api/comparisons', json={'reading_ids': ['paper', 'other'], 'dimensions': ['method']})
        assert response.status_code == 200
        assert all(not cell['supported'] and not cell['evidence'] for cell in response.json()['cells'])
