import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip('mcp', reason='Install backend/requirements-mcp.txt for MCP tests')
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_server import create_server, local_url


@pytest.mark.parametrize('url', ['https://example.com', 'http://127.0.0.1/evil',
    'http://user:secret@localhost', 'http://localhost?token=secret', 'http://localhost:bad'])
def test_only_local_backend(url):
    with pytest.raises(ValueError):
        local_url(url)


def test_protocol_workflow_and_validation(caplog):
    calls = []
    evidence = [{'page': 2, 'quote': 'Original evidence must be preserved.'}]

    def backend(req):
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        path = req.url.path
        if path == '/api/search':
            return httpx.Response(200, json={'id': 'search-1', 'papers': [{'id': 'arxiv:1234'}]})
        if path == '/api/readings/import':
            assert body == {'record_id': 'search-1', 'paper_id': 'arxiv:1234', 'mode': 'pdf'}
            return httpx.Response(200, json={'id': 'paper-1', 'title': 'Paper', 'kind': 'pdf',
                'source_url': 'https://arxiv.org/abs/1234', 'page_count': 2, 'warnings': [],
                'sections': [{'text': 'PRIVATE FULL TEXT'}]})
        if path.endswith('/questions'):
            return httpx.Response(200, json={'supported': True, 'claims': [{'text': 'Claim', 'evidence': evidence}]})
        if path.endswith('/summary'):
            return httpx.Response(200, json={'abstract': 'Summary', 'evidence': evidence})
        if path == '/api/comparisons':
            return httpx.Response(200, json={'cells': [{'supported': False, 'text': 'Insufficient evidence', 'evidence': []}]})
        if path.endswith('/paper-1'):
            return httpx.Response(200, json={'id': 'paper-1', 'title': 'Paper', 'sections': ['PRIVATE FULL TEXT']})
        if path == '/api/readings':
            return httpx.Response(200, json=[{'id': 'paper-1', 'title': 'Paper'}])
        return httpx.Response(502, json={'detail': 'PRIVATE ERROR secret-key'})

    async def run():
        server = create_server(transport=httpx.MockTransport(backend))
        async with create_connected_server_and_client_session(server) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 7
            assert next(t for t in tools if t.name == 'list_papers').annotations.readOnlyHint
            assert not next(t for t in tools if t.name == 'search_papers').annotations.readOnlyHint
            async def call(name, args):
                result = await client.call_tool(name, args)
                assert not result.isError, result
                return result.structuredContent
            search = await call('search_papers', {'query': {'plan': {'queries': ['tool use']}, 'sources': ['arxiv']}})
            doc = await call('import_paper_pdf', {'record_id': search['id'], 'paper_id': search['papers'][0]['id']})
            assert 'sections' not in doc
            answer = await call('ask_paper', {'reading_id': doc['id'], 'question': {'question': 'What was evaluated?'}})
            assert answer['claims'][0]['evidence'] == evidence
            assert (await call('summarize_paper', {'reading_id': doc['id']}))['evidence'] == evidence
            assert 'sections' not in await call('get_paper', {'reading_id': doc['id']})
            assert (await call('list_papers', {}))['papers'][0]['id'] == doc['id']
            comparison = await call('compare_papers', {'comparison': {'reading_ids': ['paper-1', 'paper-2'], 'dimensions': ['goal']}})
            assert comparison['cells'][0]['supported'] is False
            count = len(calls)
            for name, args in [
                ('get_paper', {'reading_id': '../health'}),
                ('search_papers', {'query': {'plan': {'queries': ['x']}, 'limit': 101}}),
                ('compare_papers', {'comparison': {'reading_ids': ['a', 'a'], 'dimensions': ['goal']}}),
            ]:
                assert (await client.call_tool(name, args)).isError
            assert len(calls) == count
            failed = await client.call_tool('get_paper', {'reading_id': 'missing'})
            assert failed.isError
            assert 'secret-key' not in str(failed)
            assert len(calls) == count + 1  # No retries.
    asyncio.run(run())
    assert 'secret-key' not in caplog.text
    assert 'PRIVATE FULL TEXT' not in caplog.text


def test_cancellation_closes_backend_request():
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def backend(req):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        server = create_server(transport=httpx.MockTransport(backend))
        task = asyncio.create_task(server.call_tool('list_papers', {}))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    asyncio.run(run())


def test_stdio_discovery_without_backend():
    async def run():
        params = StdioServerParameters(command=sys.executable,
            args=['-X', 'utf8', str(Path(__file__).resolve().parents[1] / 'mcp_server.py')])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert len((await client.list_tools()).tools) == 7
    asyncio.run(run())


def test_stdio_to_real_http_backend(tmp_path):
    import socket
    import subprocess
    import time
    from app.storage import Store
    from app.papers.schemas import Reading

    db = tmp_path / 'test.db'
    Store(db).save_reading(Reading(id='fixture', title='测试论文', kind='pdf', origin='upload',
        created_at='2026-10-26', page_count=1, sections=[]))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    backend = Path(__file__).resolve().parents[1]
    code = ('import sys, uvicorn; from app.main import create_app; '
        'uvicorn.run(create_app(sys.argv[1]), host="127.0.0.1", port=int(sys.argv[2]), log_level="error")')
    process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code, str(db), str(port)],
        cwd=backend, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(50):
                try:
                    if client.get(f'http://127.0.0.1:{port}/api/health').is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail('Temporary backend did not start')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(backend / 'mcp_smoke.py'),
            '--base-url', f'http://127.0.0.1:{port}'], capture_output=True, text=True,
            encoding='utf-8', timeout=20)
        assert result.returncode == 0, result.stderr
        assert '1 library records' in result.stdout
        assert 'metadata read successfully' in result.stdout
    finally:
        process.terminate()
        process.wait(timeout=10)
