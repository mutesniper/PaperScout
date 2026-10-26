import asyncio
import io
import json
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from app.main import create_app
from app.models import CloudModel
from app.pdf_worker import parse_pdf, sections_from_pages
from app.reading import download_pdf, public_address
from app.reading import wait_operation
from app.reading_schemas import Section, Fragment
from app.schemas import SearchResult


def pdf_bytes(encrypted=False, blank=False):
    writer = PdfWriter()
    texts = [
        '1 Introduction\nOur system selects tools using a language model.\nWe compare two approaches on held-out tasks.',
        '2 Results\nThe model completed 80 of 100 tasks in this experiment.\n3 Limitations\nOnly English tasks were evaluated in this experiment.',
    ]
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        if blank:
            continue
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        commands = 'BT /F1 12 Tf 50 740 Td 18 TL ' + ' T* '.join(f'({line}) Tj' for line in text.splitlines()) + ' ET'
        stream.set_data(commands.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('test-password')
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def saved_search(client):
    record = SearchResult.model_validate({'id': 'search-fixture', 'created_at': '2026-10-23',
        'request': {'plan': {'queries': ['tools']}}, 'sources': [], 'papers': [{
            'id': 'paper', 'title': 'Test paper', 'sources': ['arxiv'], 'url': 'https://arxiv.org/abs/1234.5678',
            'abstract': 'Our system selects tools using a language model.', 'access': 'open_preprint',
            'fulltext_url': 'https://arxiv.org/pdf/1234.5678', 'evidence': 'fixture'}]})
    client.app.state.store.save(record)
    return {'record_id': record.id, 'paper_id': 'paper'}


def test_pdf_page_and_section_extraction():
    result = parse_pdf(pdf_bytes())
    assert result['page_count'] == 2
    assert [s['title'] for s in result['sections']] == ['1 Introduction', '2 Results', '3 Limitations']
    assert result['sections'][1]['fragments'][0]['page'] == 2
    assert '80 of 100' in result['sections'][1]['fragments'][0]['text']
    chunks = sections_from_pages(['x' * 20000])
    assert len(chunks) == 3
    assert all(sum(len(f['text']) for f in s['fragments']) <= 8000 for s in chunks)
    assert ''.join(f['text'] for s in chunks for f in s['fragments']) == 'x' * 20000


@pytest.mark.parametrize('data,match', [(b'<html/>', 'PDF'), (pdf_bytes(True), '加密'), (pdf_bytes(blank=True), 'OCR')])
def test_invalid_encrypted_and_scanned_pdf(data, match):
    with pytest.raises(ValueError, match=match):
        parse_pdf(data)


def test_upload_persistence_original_and_summary_evidence(tmp_path, caplog):
    path = tmp_path / 'reading.db'
    with TestClient(create_app(path)) as client:
        original = pdf_bytes()
        response = client.post('/api/readings/upload?filename=fixture.pdf', content=original,
                               headers={'Content-Type': 'application/pdf'})
        assert response.status_code == 200, response.text
        doc = response.json()
        assert doc['kind'] == 'pdf' and len(doc['sections']) == 3
        prefix = '/api/readings/' + doc['id']
        assert client.get(prefix + '/pdf').content == original
        assert client.get(prefix + '/pdf').headers['content-security-policy'] == 'sandbox'
        model = client.app.state.model
        model.base_url, model.api_key, model.name = 'https://model.test', 'fake', 'test-model'
        good = {'abstract': '本文研究语言模型选择工具的方法，并报告在一百个任务中完成八十个，实验仅覆盖英语任务。',
                'evidence': [{'page': 2, 'quote': 'The model completed 80 of 100 tasks in this experiment.'}]}
        model.summarize_document = AsyncMock(return_value=good)
        response = client.post(prefix + '/summary')
        assert response.status_code == 200, response.text
        assert response.json()['scope'] == 'fulltext'
        material = model.summarize_document.call_args.args[0]
        assert 'Only English' in str(material)
        assert 'Our system' in str(material)
        model.summarize_document = AsyncMock(return_value={**good, 'evidence': [{'page': 2, 'quote': 'Invented experimental evidence.'}]})
        assert client.post(prefix + '/summary').status_code == 502
        assert client.get(prefix).json()['document_summary']['abstract'] == good['abstract']
        model.summarize_document = AsyncMock(return_value={**good, 'abstract': {'private-text': 'must-not-appear-in-log'}})
        assert client.post(prefix + '/summary').status_code == 502
        assert "'path': 'abstract'" in caplog.text and 'string_type' in caplog.text
        assert 'must-not-appear-in-log' not in caplog.text
        assert client.get(prefix).json()['document_summary']['abstract'] == good['abstract']
        assert client.post(prefix + '/sections/s1/summary').status_code == 404
    with TestClient(create_app(path)) as client:
        assert client.get('/api/readings').json()[0]['id'] == doc['id']
        assert client.get(prefix).json()['document_summary']['abstract'] == good['abstract']
        assert client.get(prefix + '/pdf').content == original


def test_abstract_is_explicit_and_model_is_optional(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as client:
        body = saved_search(client)
        response = client.post('/api/readings/import', json={**body, 'mode': 'abstract'})
        assert response.status_code == 200
        doc = response.json()
        assert doc['kind'] == 'abstract' and doc['page_count'] == 0
        assert '未读取全文' in doc['warnings'][0]
        prefix = '/api/readings/' + doc['id']
        assert client.get(prefix + '/pdf').status_code == 404
        client.app.state.model.api_key = ''
        assert client.post(prefix + '/summary').status_code == 503
        assert client.get('/api/readings/missing').status_code == 404
        assert client.post('/api/readings/upload', content=b'not-pdf').status_code == 422
        assert client.post('/api/readings/import', json={**body, 'paper_id': 'missing', 'mode': 'pdf'}).status_code == 404


def test_public_import_retains_source_and_failure_does_not_claim_paywall(tmp_path, monkeypatch):
    download = AsyncMock(return_value=(pdf_bytes(), 'https://arxiv.org/pdf/1234.5678'))
    monkeypatch.setattr('app.reading.download_pdf', download)
    with TestClient(create_app(tmp_path / 'db')) as client:
        body = {**saved_search(client), 'mode': 'pdf'}
        result = client.post('/api/readings/import', json=body)
        assert result.status_code == 200, result.text
        assert result.json()['origin'] == 'public'
        assert result.json()['source_url'] == 'https://arxiv.org/pdf/1234.5678'
        download.side_effect = httpx.ConnectError('secret-internal-details')
        failed = client.post('/api/readings/import', json=body)
        assert failed.status_code == 502
        assert '不表示论文付费' in failed.json()['detail']
        assert 'secret' not in failed.text


@pytest.mark.parametrize('url', ['http://example.org/a.pdf', 'https://127.0.0.1/a', 'https://[::1]/a', 'https://u:p@example.org/a', 'https://example.org:8000/a'])
def test_download_blocks_non_public_targets(url):
    with pytest.raises(ValueError):
        asyncio.run(public_address(url))


def test_fake_ip_has_specific_diagnostic_and_no_request(tmp_path, monkeypatch):
    from app.reading import DownloadAddressError
    async def check():
        loop = asyncio.get_running_loop()
        async def resolve(*args, **kwargs):
            return [(2, 1, 6, '', ('198.18.0.163', 443))]
        monkeypatch.setattr(loop, 'getaddrinfo', resolve)
        with pytest.raises(DownloadAddressError, match='Fake-IP'):
            await public_address('https://arxiv.org/pdf/2510.03847')
    asyncio.run(check())
    monkeypatch.setattr('app.reading.download_pdf', AsyncMock(side_effect=DownloadAddressError('Fake-IP DNS 配置问题')))
    with TestClient(create_app(tmp_path / 'db')) as client:
        response = client.post('/api/readings/import', json={**saved_search(client), 'mode': 'pdf'})
        assert response.status_code == 502
        assert 'Fake-IP' in response.json()['detail'] and '不表示论文付费' in response.json()['detail']


def test_download_pins_dns_checks_redirect_and_size(monkeypatch):
    seen = []
    async def address(url):
        if 'private' in url:
            raise ValueError('private')
        return 'papers.test', '93.184.216.34'
    monkeypatch.setattr('app.reading.public_address', address)
    async def check():
        def handler(request):
            seen.append(request)
            if request.url.path == '/redirect':
                return httpx.Response(302, headers={'location': 'https://private.test/pdf'})
            if request.url.path == '/big':
                return httpx.Response(200, headers={'content-length': str(30 * 1024 * 1024)})
            if request.url.path == '/html':
                return httpx.Response(200, content=b'<html>login</html>')
            return httpx.Response(200, content=pdf_bytes())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data, url = await download_pdf('https://papers.test/paper', client)
            assert data.startswith(b'%PDF-') and url == 'https://papers.test/paper'
            assert seen[0].url.host == '93.184.216.34'
            assert seen[0].headers['host'] == 'papers.test'
            assert seen[0].extensions['sni_hostname'] == 'papers.test'
            _, upgraded = await download_pdf('http://papers.test/paper', client)
            assert upgraded == 'https://papers.test/paper'
            assert seen[-1].url.scheme == 'https'
            for suffix in ('redirect', 'big', 'html'):
                with pytest.raises(ValueError):
                    await download_pdf('https://papers.test/' + suffix, client)
    asyncio.run(check())


def test_cloud_summary_sends_only_selected_section_and_handles_truncation():
    async def check():
        def handler(request):
            sent = json.loads(request.content)
            material = json.loads(sent['messages'][1]['content'])
            assert material['kind'] == 'abstract'
            assert material['section']['fragments'][0]['text'] == 'Fixture evidence only.'
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = CloudModel(client, 'https://test/v1', 'fake', 'test')
            with pytest.raises(ValueError, match='truncated'):
                await model.summarize(Section(id='abstract', title='Abstract', fragments=[Fragment(page=1, text='Fixture evidence only.')]), 'abstract')
    asyncio.run(check())


def test_disconnected_reading_request_cancels_operation():
    from fastapi import HTTPException
    async def check():
        request = type('Request', (), {'is_disconnected': AsyncMock(return_value=True)})()
        async def operation():
            await asyncio.sleep(10)
        with pytest.raises(HTTPException) as error:
            await wait_operation(request, operation(), 20)
        assert error.value.status_code == 499
    asyncio.run(check())


def test_long_document_uses_all_fragments_and_synthesizes_once():
    from app.reading import build_document_summary
    from app.reading_schemas import Reading
    async def check():
        fragments = [Fragment(page=i + 1, text=(f'Evidence for part {i}. ' * 500)) for i in range(6)]
        doc = Reading(id='long', title='Long', kind='pdf', origin='upload', created_at='now', page_count=6,
            sections=[Section(id='all', title='all', fragments=fragments)])
        async def extract(section, kind):
            f = section.fragments[-1]
            return {'points': [{'text': '资料要点', 'evidence': [{'page': f.page, 'quote': f.text[:30]}]}]}
        model = type('Model', (), {})()
        model.summarize = AsyncMock(side_effect=extract)
        model.summarize_document = AsyncMock(return_value={'abstract': '这是根据全部论文内容综合生成的全文摘要，并包含最后部分的信息。',
            'evidence': [{'page': 6, 'quote': fragments[-1].text[:30]}]})
        result = await build_document_summary(doc, model)
        assert result.evidence[0].page == 6
        assert model.summarize.await_count > 1
        assert model.summarize_document.await_count == 1
        seen = [f for call in model.summarize.call_args_list for f in call.args[0].fragments]
        for original in fragments:
            assert ''.join(f.text for f in seen if f.page == original.page) == original.text
        model.summarize.side_effect = httpx.ConnectError('failed chunk')
        model.summarize_document.reset_mock()
        with pytest.raises(httpx.ConnectError):
            await build_document_summary(doc, model)
        model.summarize_document.assert_not_called()
    asyncio.run(check())


def test_summary_truncation_does_not_repeat_or_increase_spend(monkeypatch):
    monkeypatch.delenv('LLM_SUMMARY_MAX_TOKENS', raising=False)
    async def check():
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = CloudModel(client, 'https://ws-test.cn-beijing.maas.aliyuncs.com/v1', 'fake', 'deepseek-v4.1-flash')
            with pytest.raises(ValueError, match='truncated'):
                await model.summarize(Section(id='s', title='t', fragments=[Fragment(page=1, text='sample')]), 'pdf')
            assert len(requests) == 1
            assert requests[0]['max_tokens'] == 4096
            assert requests[0]['enable_thinking'] is False
            assert CloudModel(client, 'https://example.test/v1', 'fake', 'deepseek-v4.1-flash')._summary_options() == {}
    asyncio.run(check())


def test_verified_long_quote_shortened_but_invented_suffix_not_hidden():
    from app.reading import normalize_summary_evidence
    text = 'Actual source sentence. ' * 40
    fragments = [Fragment(page=1, text=text)]
    original = {'points': [{'text': 'summary', 'evidence': [{'page': 1, 'quote': text}]}]}
    normalized = normalize_summary_evidence(original, fragments)
    assert len(normalized['points'][0]['evidence'][0]['quote']) == 500
    assert original['points'][0]['evidence'][0]['quote'] == text
    original['points'][0]['evidence'][0]['quote'] = text + 'Invented conclusion.'
    assert normalize_summary_evidence(original, fragments) == original


def test_model_evidence_ids_resolve_original_text_and_reject_invented_ids():
    catalog = CloudModel._catalog([{'page': 3, 'text': 'Exact evidence with original wording.'}])
    result = CloudModel._resolve_evidence({'abstract': 'summary', 'evidence_ids': ['e1']}, catalog)
    assert result['evidence'] == [{'page': 3, 'quote': 'Exact evidence with original wording.'}]
    with pytest.raises(ValueError, match='unknown evidence ID'):
        CloudModel._resolve_evidence({'abstract': 'summary', 'evidence_ids': ['e999']}, catalog)
    long = 'A long source sentence with facts. ' * 80
    entries = CloudModel._catalog([{'page': 2, 'text': long}])
    assert ' '.join(e['quote'] for e in entries.values()).split() == long.split()
    assert all(len(e['quote']) <= 400 for e in entries.values())


def test_medium_paper_uses_single_call_without_omitting_text():
    from app.reading import build_document_summary
    from app.reading_schemas import Reading
    async def check():
        text = 'Actual source evidence. ' * 1300
        doc = Reading(id='medium', title='Medium', kind='pdf', origin='upload', created_at='now', page_count=1,
            sections=[Section(id='s', title='s', fragments=[Fragment(page=1, text=text)])])
        model = type('Model', (), {})()
        model.summarize = AsyncMock()
        model.summarize_document = AsyncMock(return_value={'abstract': '这是一份针对整篇论文生成的摘要，包含研究方法与结果。', 'evidence': [{'page': 1, 'quote': 'Actual source evidence.'}]})
        await build_document_summary(doc, model)
        model.summarize.assert_not_called()
        model.summarize_document.assert_awaited_once()
        assert model.summarize_document.call_args.args[0]['fragments'][0]['text'] == text
    asyncio.run(check())
