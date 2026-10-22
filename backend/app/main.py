import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from .models import CloudModel, ANALYSIS_TOTAL_TIMEOUT
from .model_output import ModelOutputError
from .providers import Sources, deduplicate
from .schemas import Idea, AnalysisRequest, Paper, SearchRequest, SearchResult, SourceResult
from .storage import Store
from .ranking import source_candidates, rank_candidates, ranking_details

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
logger = logging.getLogger('paperscout')
CANDIDATE_LIMIT = 100

def create_app(db_path=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(Path(db_path) if db_path else ROOT / 'backend/data/paperscout.db')
        async with httpx.AsyncClient(timeout=12, headers={'User-Agent': 'PaperScout/0.1 (personal research assistant)'}) as client:
            app.state.sources = Sources(client, os.getenv('SEMANTIC_SCHOLAR_API_KEY', ''))
            app.state.model = CloudModel(client, os.getenv('LLM_BASE_URL', ''), os.getenv('LLM_API_KEY', ''), os.getenv('LLM_MODEL', ''))
            yield

    app = FastAPI(title='PaperScout', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'], allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'model_configured': app.state.model.configured, 'model': app.state.model.name or None}

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

    @app.post('/api/analyze')
    async def analyze(idea: AnalysisRequest):
        try:
            async with asyncio.timeout(ANALYSIS_TOTAL_TIMEOUT):
                result = await app.state.model.analyze(idea.text, idea.clarification)
            return {'analysis': result, 'model': app.state.model.name}
        except RuntimeError:
            raise HTTPException(503, '尚未配置云端模型，请先使用手动关键词检索。') from None
        except ModelOutputError as exc:
            reasons = {'truncated': '模型回复被长度限制截断，请减少目标范围或更换模型。',
                       'empty': '模型未返回正文，请检查模型是否支持当前接口。',
                       'refused': '模型未能处理该目标，请调整描述。',
                       'envelope': '模型服务响应结构不兼容，请检查服务接口配置。',
                       'json': '模型未返回有效 JSON，自动重试后仍失败，请重试或更换模型。',
                       'schema': '模型返回字段不符合要求，自动重试后仍失败；后台日志已记录字段位置。'}
            raise HTTPException(502, reasons[exc.code]) from None
        except (ValidationError, ValueError, KeyError, TypeError, IndexError):
            raise HTTPException(502, '模型返回的目标分析不符合格式，请重试或补充目标描述。') from None
        except (httpx.TimeoutException, TimeoutError) as exc:
            logger.warning('analysis_request code=timeout error_type=%s', type(exc).__name__)
            raise HTTPException(504, '模型生成超时。请稍后重试，或使用响应更快的模型；这不一定是密钥配置错误。') from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning('analysis_request code=upstream_http status=%s', status)
            reason = ('模型服务认证失败，请检查密钥和模型访问权限。' if status in (401, 403) else
                      '模型服务限流，请稍后重试。' if status == 429 else
                      '模型服务拒绝了请求参数，请核对模型名称及输出 Token 上限。' if status in (400, 422) else
                      '模型服务暂时不可用，请稍后重试。')
            raise HTTPException(502, reason) from None
        except httpx.HTTPError as exc:
            logger.warning('analysis_request code=transport error_type=%s', type(exc).__name__)
            raise HTTPException(502, '无法连接模型服务，请检查网络和服务地址。') from None

    async def collect(body):
        # Treat all lines as one combined search, not independent alternatives.
        query = ' '.join(body.plan.queries)
        async def one(source):
            key = hashlib.sha256(json.dumps(['v3-combined', source, query, body.plan.year_from,
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
