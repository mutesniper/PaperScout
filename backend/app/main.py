import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from itertools import zip_longest
from uuid import uuid4
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from .models import CloudModel
from .providers import Sources, deduplicate
from .schemas import Idea, Paper, SearchRequest, SearchResult, SourceResult
from .storage import Store
from .ranking import source_candidates, rank_candidates, ranking_details
from .reading import router as reading_router
from .qa import router as qa_router
from .retrieval import CloudEmbedding

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
logger = logging.getLogger('paperscout')
CANDIDATE_LIMIT = 100

def create_app(db_path=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(Path(db_path) if db_path else ROOT / 'backend/data/paperscout.db')
        app.state.reading_slots = asyncio.Semaphore(2)
        app.state.embedding_lock = asyncio.Lock()
        async with httpx.AsyncClient(timeout=12, headers={'User-Agent': 'PaperScout/0.1 (personal research assistant)'}) as client:
            app.state.sources = Sources(client, os.getenv('SEMANTIC_SCHOLAR_API_KEY', ''))
            app.state.model = CloudModel(client, os.getenv('LLM_BASE_URL', ''), os.getenv('LLM_API_KEY', ''), os.getenv('LLM_MODEL', ''))
            app.state.embedding = CloudEmbedding(client, os.getenv('EMBEDDING_BASE_URL', ''), os.getenv('EMBEDDING_API_KEY', ''), os.getenv('EMBEDDING_MODEL', ''))
            yield

    app = FastAPI(title='PaperScout', lifespan=lifespan)
    app.include_router(reading_router)
    app.include_router(qa_router)
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'], allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'model_configured': app.state.model.configured, 'model': app.state.model.name or None,
            'embedding_configured': app.state.embedding.configured, 'embedding_partial': app.state.embedding.partial}

    @app.post('/api/plan')
    async def plan(idea: Idea):
        try:
            async with asyncio.timeout(45):
                result = await app.state.model.plan(idea.text)
            return {'plan': result, 'model': app.state.model.name}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        except (ValidationError, ValueError, KeyError, TypeError, IndexError):
            raise HTTPException(502, '模型返回的检索计划不符合格式，请重试或手动填写。') from None
        except (httpx.HTTPError, TimeoutError):
            raise HTTPException(502, '云端模型请求失败，请检查配置或使用手动检索。') from None

    async def collect(body):
        async def one_query(source, query):
            key = hashlib.sha256(json.dumps(['v4-independent', source, query, body.plan.year_from,
                body.plan.year_to, CANDIDATE_LIMIT], sort_keys=True).encode()).hexdigest()
            cached = app.state.store.cached(key)
            try:
                if cached is not None:
                    batch = [Paper.model_validate(p) for p in cached]
                else:
                    async with asyncio.timeout(28):
                        batch = await app.state.sources.fetch(source, query, body.plan, CANDIDATE_LIMIT)
                    # Enforce the cap even if a provider ignores its limit parameter.
                    batch = batch[:CANDIDATE_LIMIT]
                    app.state.store.cache(key, [p.model_dump(mode='json') for p in batch])
                eligible = [p for p in batch[:CANDIDATE_LIMIT]
                    if (not body.plan.year_from or p.year is not None and p.year >= body.plan.year_from)
                    and (not body.plan.year_to or p.year is not None and p.year <= body.plan.year_to)]
                papers = source_candidates(eligible, source)
                return papers, SourceResult(source=source, status='ok', count=len(papers), cached=cached is not None)
            except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                reason = '来源限流，请稍后再试或配置来源 API 密钥' if code == 429 else '来源请求超时' if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else '来源暂不可用或响应格式异常'
                logger.warning('source=%s error_type=%s status=%s', source, type(exc).__name__, code)
                return [], SourceResult(source=source, status='error', count=0, message=reason)
        async def one(source):
            # Sequential queries respect provider limits; different sources run concurrently.
            results = [await one_query(source, query) for query in body.plan.queries]
            # Round-robin ensures later query lines get a place in the candidate pool.
            interleaved = [paper for row in zip_longest(*(rows for rows, _ in results))
                           for paper in row if paper is not None]
            papers = source_candidates(deduplicate(interleaved)[:CANDIDATE_LIMIT], source)
            failures = [status for _, status in results if status.status == 'error']
            status = 'error' if len(failures) == len(results) else 'partial' if failures else 'ok'
            message = ''
            if failures:
                reasons = '；'.join(dict.fromkeys(item.message for item in failures))
                message = f'{len(failures)}/{len(results)} 组检索失败：{reasons}'
            return papers, SourceResult(source=source, status=status, count=len(papers),
                cached=all(item.cached for _, item in results), message=message)

        groups = await asyncio.gather(*(one(s) for s in dict.fromkeys(body.sources)))
        candidates = deduplicate([p for group, _ in groups for p in group])
        ranked = rank_candidates(candidates, body.sort)
        note, warnings = ranking_details(candidates, body.sort)
        return SearchResult(id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(), request=body,
            papers=ranked[:body.limit], sources=[status for _, status in groups],
            candidate_count=len(candidates), ranking_note=note, warnings=warnings)

    @app.post('/api/search', response_model=SearchResult)
    async def search(body: SearchRequest, request: Request):
        task = asyncio.create_task(collect(body))
        try:
            async with asyncio.timeout(95):
                while not task.done():
                    if await request.is_disconnected():
                        raise HTTPException(499, '检索已取消')
                    await asyncio.wait({task}, timeout=0.2)
                result = await task
            app.state.store.save(result)
            return result
        except TimeoutError:
            raise HTTPException(504, '检索超时，请减少检索词或稍后重试。') from None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @app.get('/api/history')
    def history():
        return app.state.store.history()

    @app.get('/api/history/{record_id}', response_model=SearchResult)
    def record(record_id: str):
        result = app.state.store.get(record_id)
        if result is None:
            raise HTTPException(404, '检索记录不存在')
        return result

    return app

app = create_app()
