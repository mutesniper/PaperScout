import asyncio
from copy import deepcopy
import ipaddress
import json
import logging
import socket
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from datetime import datetime, timezone
from uuid import uuid4
import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError
from app.papers.schemas import ImportPaper, Reading, SummaryContent, Section, Fragment, DocumentSummaryContent, DocumentSummary
from app.papers.pdf_worker import MAX_BYTES
from app.search.providers import with_preprint_link

router = APIRouter(prefix='/api/readings')


class DownloadAddressError(ValueError):
    """Safe diagnostic for resolver/policy failures, without credentials."""


async def wait_operation(request, operation, timeout):
    task = asyncio.create_task(operation)
    try:
        async with asyncio.timeout(timeout):
            while not task.done():
                if await request.is_disconnected():
                    raise HTTPException(499, '阅读请求已取消。')
                await asyncio.wait({task}, timeout=0.2)
            return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def extract_pdf(data):
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app.papers.pdf_worker',
        cwd=Path(__file__).resolve().parents[2], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(40):
            output, _ = await process.communicate(data)
        parsed = json.loads(output)
        if 'error' in parsed:
            raise HTTPException(422, parsed['error'])
        return parsed
    except (TimeoutError, ValueError):
        raise HTTPException(422, 'PDF 解析超时或失败，请尝试更小的文件。') from None
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def public_address(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('仅允许 HTTPS 公开 PDF 地址。')
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise DownloadAddressError('PDF 域名 DNS 解析失败，请检查本机网络或 DNS 配置。') from None
    ips = [row[4][0] for row in addresses]
    if any(ipaddress.ip_address(ip) in ipaddress.ip_network('198.18.0.0/15') for ip in ips):
        raise DownloadAddressError('PDF 域名被解析为 Fake-IP（198.18.0.0/15），当前公网下载检查无法使用该地址。请在代理/DNS 中让论文域名返回真实公网 IP，或手动上传 PDF。')
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise DownloadAddressError('PDF 域名解析到非公网地址，下载已阻止。请检查 DNS 或手动上传 PDF。')
    return parts.hostname, ips[0]


async def download_pdf(url, client):
    # arXiv metadata often contains legacy HTTP links. Try HTTPS on the same host;
    # never send an HTTP request or follow a later downgrade redirect.
    initial = httpx.URL(url)
    if initial.scheme == 'http' and initial.port in (None, 80):
        url = str(initial.copy_with(scheme='https', port=None))
    # Pin the validated IP at connection time; keep the original TLS name and Host.
    # This also prevents a public-to-private DNS rebind between validation and fetch.
    for _ in range(5):
        hostname, address = await public_address(url)
        target = httpx.URL(url).copy_with(host=address)
        async with client.stream('GET', target, headers={'Host': hostname},
                extensions={'sni_hostname': hostname}, follow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('location')
                if not location:
                    raise ValueError('全文链接重定向无效。')
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            if int(response.headers.get('content-length', '0')) > MAX_BYTES:
                raise ValueError('PDF 超过 20 MB，请下载后拆分上传。')
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError('PDF 超过 20 MB，请下载后拆分上传。')
            if not data.startswith(b'%PDF-'):
                raise ValueError('链接返回的不是 PDF，可能是网页或访问验证页面；可手动上传公开 PDF。')
            return bytes(data), url
    raise ValueError('全文链接重定向过多。')


async def download_with_retry(url, client):
    """One timeout retry; caller retains the shared total deadline and cancellation."""
    for attempt in range(2):
        try:
            async with asyncio.timeout(25):
                return await download_pdf(url, client)
        except (httpx.TimeoutException, TimeoutError):
            if attempt:
                raise
            logging.getLogger(__name__).info('pdf_download timeout; retry=1')
            await asyncio.sleep(0.5)


def get_reading(request, reading_id):
    doc = request.app.state.store.reading(reading_id)
    if doc is None:
        raise HTTPException(404, '阅读记录不存在。')
    return doc


async def save_pdf(request, data, title, origin, source_url=None):
    # Bound concurrent parsing and isolate malformed documents from the API process.
    async with request.app.state.reading_slots:
        parsed = await wait_operation(request, extract_pdf(data), 45)
    doc = Reading(id=str(uuid4()), title=title, kind='pdf', origin=origin,
        source_url=source_url, created_at=datetime.now(timezone.utc).isoformat(), **parsed)
    directory = request.app.state.store.path.parent / 'pdfs'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{doc.id}.pdf'
    path.write_bytes(data)
    try:
        request.app.state.store.save_reading(doc)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return doc


@router.get('')
def readings(request: Request):
    return request.app.state.store.reading_history()


@router.post('/upload', response_model=Reading)
async def upload(request: Request, filename: str = Query(default='论文.pdf', min_length=1, max_length=250)):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, '文件超过 20 MB。')
    if not data.startswith(b'%PDF-'):
        raise HTTPException(422, '请选择有效的 PDF 文件。')
    return await save_pdf(request, bytes(data), filename, 'upload')


@router.post('/import', response_model=Reading)
async def import_paper(body: ImportPaper, request: Request):
    record = request.app.state.store.get(body.record_id)
    paper = next((p for p in record.papers if p.id == body.paper_id), None) if record else None
    if paper is None:
        raise HTTPException(404, '未找到论文，请重新检索后再试。')
    paper = with_preprint_link(paper)
    if body.mode == 'abstract':
        if not paper.abstract.strip():
            raise HTTPException(422, '来源未提供摘要。')
        if len(paper.abstract) > 20_000:
            raise HTTPException(422, '摘要过长，暂不支持。')
        doc = Reading(id=str(uuid4()), title=paper.title, kind='abstract', origin='abstract',
            source_url=paper.url, created_at=datetime.now(timezone.utc).isoformat(), page_count=0,
            sections=[Section(id='abstract', title='来源摘要', fragments=[Fragment(page=1, text=paper.abstract)])],
            warnings=['仅基于来源摘要，未读取全文；证据定位为摘要，不是 PDF 页码。'])
        request.app.state.store.save_reading(doc)
        return doc
    if not paper.fulltext_url or paper.access not in ('open_fulltext', 'open_preprint'):
        raise HTTPException(422, '没有公开全文链接，可以上传你有权使用的 PDF。')
    try:
        async with asyncio.timeout(50):
            # No environment proxies or credentials are forwarded to paper hosts.
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                data, url = await wait_operation(request, download_with_retry(paper.fulltext_url, client), 50)
    except DownloadAddressError as exc:
        raise HTTPException(502, str(exc) + ' 此结果不表示论文付费。') from None
    except (httpx.TimeoutException, TimeoutError):
        raise HTTPException(502, '公开 PDF 下载超时，请检查后端网络连接或手动上传。此结果不表示论文付费。') from None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f'PDF 网站返回 HTTP {exc.response.status_code}，可能限制自动访问；可打开原链接后手动上传。此结果不表示论文付费。') from None
    except (ValueError, httpx.HTTPError, OSError):
        raise HTTPException(502, '公开 PDF 获取失败或文件不受支持；可打开原链接后手动上传。此结果不表示论文付费。') from None
    return await save_pdf(request, data, paper.title, 'public', url)


@router.get('/{reading_id}', response_model=Reading)
def reading(reading_id: str, request: Request):
    return get_reading(request, reading_id)


@router.get('/{reading_id}/pdf')
def pdf(reading_id: str, request: Request):
    doc = get_reading(request, reading_id)
    path = request.app.state.store.path.parent / 'pdfs' / f'{doc.id}.pdf'
    if doc.kind != 'pdf' or not path.is_file():
        raise HTTPException(404, '此记录没有 PDF 文件。')
    return FileResponse(path, media_type='application/pdf', headers={
        'Content-Security-Policy': "sandbox", 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f'inline; filename="{doc.id}.pdf"'})


def check_evidence(evidence, fragments):
    for item in evidence:
        quote = ' '.join(item.quote.split())
        if len(quote) < 10 or not any(f.page == item.page and quote in ' '.join(f.text.split()) for f in fragments):
            raise ValueError('unverifiable evidence')


def normalize_summary_evidence(payload, fragments):
    # A long, verified verbatim quote can safely be shortened for display. Never
    # shorten an unmatched quote: that could hide an invented suffix.
    result = deepcopy(payload)
    if not isinstance(result, dict):
        return result
    containers = result.get('points', []) if 'points' in result else [result]
    if not isinstance(containers, list):
        return result
    for container in containers:
        if not isinstance(container, dict) or not isinstance(container.get('evidence'), list):
            continue
        for item in container['evidence']:
            if not isinstance(item, dict) or not isinstance(item.get('quote'), str):
                continue
            quote = ' '.join(item['quote'].split())
            if len(item['quote']) > 500 and any(f.page == item.get('page') and quote in ' '.join(f.text.split()) for f in fragments):
                item['quote'] = quote[:500]
    return result


async def build_document_summary(doc, model):
    # Every extracted fragment participates; never silently summarize only a prefix.
    fragments = [fragment for section in doc.sections for fragment in section.fragments]
    groups, current, size = [], [], 0
    for fragment in fragments:
        for start in range(0, len(fragment.text), 8000):
            part = Fragment(page=fragment.page, text=fragment.text[start:start + 8000])
            if current and size + len(part.text) > 48000:
                groups.append(current)
                current, size = [], 0
            current.append(part)
            size += len(part.text)
    if current:
        groups.append(current)
    if not groups:
        raise ValueError('empty document')
    if len(groups) == 1:
        material = {'fragments': [f.model_dump() for f in fragments]}
    else:
        notes = []
        for index, group in enumerate(groups):
            raw = await model.summarize(
                Section(id=f'part-{index}', title='Full paper evidence extraction', fragments=group), doc.kind)
            result = SummaryContent.model_validate(normalize_summary_evidence(raw, group))
            check_evidence([e for p in result.points for e in p.evidence], group)
            notes.append(result.model_dump())
        material = {'complete_document_notes': notes}
    raw = await model.summarize_document(material, doc.kind)
    result = DocumentSummaryContent.model_validate(normalize_summary_evidence(raw, fragments))
    check_evidence(result.evidence, fragments)
    return result


@router.post('/{reading_id}/summary', response_model=DocumentSummary)
async def summarize(reading_id: str, request: Request):
    doc = get_reading(request, reading_id)
    model = request.app.state.model
    if not model.configured:
        raise HTTPException(503, '尚未配置云端模型，请配置后生成全文摘要。')
    try:
        result = await wait_operation(request, build_document_summary(doc, model), 1800)
    except (ValueError, KeyError, TypeError, IndexError, ValidationError) as exc:
        reason = str(exc) if type(exc) is ValueError else ''
        code = 'truncated' if reason == 'truncated summary' else 'evidence' if reason == 'unverifiable evidence' else 'format'
        # Log only schema paths/error categories, never provider text or source input.
        fields = []
        if isinstance(exc, ValidationError):
            fields = [{ 'path': '.'.join(str(p) for p in error['loc']), 'type': error['type'] }
                      for error in exc.errors(include_input=False, include_url=False)]
        known = {'invalid summary format', 'invalid evidence format', 'invalid evidence IDs',
                 'unknown evidence ID', 'evidence too short', 'empty document',
                 'truncated summary', 'unverifiable evidence', 'invalid summary content'}
        detail = reason if reason in known else type(exc).__name__
        logging.getLogger('paperscout').warning('document_summary reading_id=%s code=%s detail=%s fields=%s',
            reading_id, code, detail, fields)
        message = {
            'truncated': '模型回复达到输出上限，已停止且未自动重试，避免重复消耗。请检查模型思考模式或输出额度后重试；未保存不完整摘要。',
            'evidence': '模型引用的原句或页码与提取文本不匹配，请重新生成；未保存未经校验的摘要。',
            'format': '模型返回的摘要格式不符合要求，请重试；未保存不完整摘要。',
        }[code]
        raise HTTPException(502, message) from None
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(502, '全文摘要生成失败或超时，请检查模型服务后重试；已有摘要保持不变。') from None
    summary = DocumentSummary(**result.model_dump(), model=model.name,
        scope='fulltext' if doc.kind == 'pdf' else 'abstract',
        created_at=datetime.now(timezone.utc).isoformat())
    request.app.state.store.save_document_summary(reading_id, summary)
    return summary
