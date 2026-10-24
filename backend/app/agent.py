"""Bounded model-directed tool loop, with durable local task records."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict
from uuid import uuid4
import httpx
from fastapi import APIRouter, HTTPException, Request
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from .agent_budget import Budget, BudgetExceeded, current_budget
from .schemas import SearchRequest, Plan
from .reading_schemas import ImportPaper
from .reading import import_paper
from .comparison import ComparisonRequest, compare
from .qa import Question, ask
from .providers import deduplicate
from .ranking import rank_candidates

router = APIRouter(prefix='/api/agent/runs')
MAX_STEPS = 10
TOTAL_TIMEOUT = 600
ARXIV_CANDIDATE_TARGET = 5


async def search_arxiv_first(app, plan, event):
    # One focused query per action; the agent can revise it on its second search.
    focused = plan.model_copy(update={'queries': plan.queries[:1]})
    event('search', 'running', '优先搜索 arXiv，使用一组核心检索词。')
    primary = await app.state.collect_search(SearchRequest(plan=focused, sources=['arxiv'], limit=100))
    pool = deduplicate(primary.papers)
    statuses = primary.sources
    sources = ['arxiv']
    warnings = list(primary.warnings)
    public_count = sum(bool(p.fulltext_url and p.access in ('open_fulltext', 'open_preprint')) for p in pool)
    if public_count < ARXIV_CANDIDATE_TARGET or any(s.status != 'ok' for s in statuses):
        event('search', 'running', f'arXiv 提供 {public_count} 篇带公开全文链接的候选，或来源请求未完整成功；补充搜索 Semantic Scholar。')
        secondary = await app.state.collect_search(SearchRequest(plan=focused, sources=['semantic_scholar'], limit=100))
        pool = deduplicate(pool + secondary.papers)
        statuses = statuses + secondary.sources
        sources.append('semantic_scholar')
        warnings.extend(secondary.warnings)
    else:
        event('search', 'ok', f'arXiv 已提供 {public_count} 篇带公开全文链接的候选，跳过 Semantic Scholar。')
    return primary.model_copy(update={
        'request': SearchRequest(plan=focused, sources=sources, limit=10),
        'papers': rank_candidates(pool, 'relevance')[:10],
        'sources': statuses, 'candidate_count': len(pool), 'warnings': list(dict.fromkeys(warnings)),
        'ranking_note': 'Agent 每次使用一组检索词，优先 arXiv；公开全文候选不足 5 篇或来源失败时补充 Semantic Scholar，合并去重后按 RRF 返回前 10 篇。',
    })


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class StartRun(Strict):
    goal: str = Field(min_length=2, max_length=3000)
    reading_ids: list[str] = Field(default_factory=list, max_length=5)


class Action(Strict):
    reason: str = Field(min_length=1, max_length=200)


class SearchAction(Action):
    tool: Literal['search']
    plan: Plan


class ReadAction(Action):
    tool: Literal['read']
    paper_id: str


class CompareAction(Action):
    tool: Literal['compare']
    args: ComparisonRequest


class AnswerAction(Action):
    tool: Literal['answer']
    reading_id: str
    question: str = Field(min_length=2, max_length=1500)


class FinishAction(Action):
    tool: Literal['finish']


ACTION = TypeAdapter(Annotated[SearchAction | ReadAction | CompareAction | AnswerAction | FinishAction,
                               Field(discriminator='tool')])


class State(TypedDict, total=False):
    action: dict
    done: bool


class JobRequest:
    """Internal service context; job cancellation is owned by the task runner."""
    def __init__(self, app):
        self.app = app

    async def is_disconnected(self):
        return False


async def execute_run(app, run):
    store, model = app.state.store, app.state.model
    budget = Budget()
    token = current_budget.set(budget)
    request = JobRequest(app)
    counts = {'search': 0, 'read': 0, 'compare': 0, 'answer': 0}
    limits = {'search': 2, 'read': 5, 'compare': 1, 'answer': 2}
    attempted = set()
    candidates = {}
    rejected_in_a_row = 0
    read_outcomes = {}

    def persist():
        run['usage'] = asdict(budget)
        store.save_agent_run(run)

    def event(tool, status, message):
        run['events'].append({'tool': tool, 'status': status, 'message': message,
                              'at': datetime.now(timezone.utc).isoformat()})
        persist()

    async def decide(state):
        if state.get('done'):
            return {'done': True}
        if run['steps'] >= MAX_STEPS:
            run.update(status='limited', message='已达到执行步数上限，保留已有结果。')
            return {'done': True}
        run['steps'] += 1
        event('agent', 'running', '正在选择下一步操作。')
        context = {'date': datetime.now(timezone.utc).date().isoformat(), 'goal': run['goal'],
            'available_pdfs': run['readings'], 'candidates': list(candidates.values())[:20],
            'events': run['events'][-16:], 'remaining': {key: limits[key] - counts[key] for key in limits},
            'attempted_actions': list(attempted), 'read_outcomes': read_outcomes,
            'has_comparison': bool(run['comparison']), 'answered_questions': [a['question'] for a in run['answers']]}
        action = ACTION.validate_python(await model.agent_decide(context, ACTION.json_schema()))
        if action.tool == 'search':
            action.plan = action.plan.model_copy(update={'queries': action.plan.queries[:1]})
        if action.tool == 'finish':
            # Research conclusions are rendered only from checked tool results.
            partial = any(e['status'] in ('error', 'partial') for e in run['events']) or not (run['papers'] or run['comparison'] or run['answers'])
            run.update(status='partial' if partial else 'completed',
                       message='任务已结束，请查看实际获取的论文、证据结果及执行记录。' if not partial else
                       '任务已结束，但部分操作失败或未获得结果；请查看缺失说明，可上传 PDF 后重试。')
            event('finish', 'ok', action.reason)
            return {'done': True}
        return {'action': action.model_dump(mode='json'), 'done': False}

    async def execute(state):
        nonlocal rejected_in_a_row
        action = ACTION.validate_python(state['action'])
        tool = action.tool
        signature = action.model_dump_json(exclude={'reason'})
        if signature in attempted or counts[tool] >= limits[tool]:
            rejected_in_a_row += 1
            target = candidates.get(action.paper_id, {}).get('title', action.paper_id) if tool == 'read' else ''
            event(tool, 'error', (target + '：' if target else '') + '该操作已执行过或已达到调用上限，请选择其他操作或结束。')
            if rejected_in_a_row >= 2:
                run.update(status='limited', message='模型连续重复无效操作，已提前停止，避免继续消耗调用额度。请查看具体失败原因。')
                return {'done': True}
            return {}
        rejected_in_a_row = 0
        attempted.add(signature)
        counts[tool] += 1  # Failed calls also consume the allowance.
        label = candidates.get(action.paper_id, {}).get('title', action.paper_id) if tool == 'read' else ''
        event(tool, 'running', (label + '：' if label else '') + action.reason)
        try:
            if tool == 'search':
                result = await search_arxiv_first(app, action.plan, event)
                store.save(result)
                run['search_ids'].append(result.id)
                for paper in result.papers:
                    candidates[paper.id] = {'paper_id': paper.id, 'record_id': result.id,
                        'title': paper.title, 'abstract': paper.abstract[:600], 'url': paper.url,
                        'public_pdf': bool(paper.fulltext_url and paper.access in ('open_fulltext', 'open_preprint'))}
                run['papers'] = list(candidates.values())[:20]
                failures = [source.message for source in result.sources if source.status != 'ok']
                event(tool, 'ok' if not failures else 'partial',
                    f'获取 {len(result.papers)} 篇候选。' + '；'.join(failures))
            elif tool == 'read':
                paper = candidates.get(action.paper_id)
                if not paper or not paper['public_pdf']:
                    raise HTTPException(422, '该论文不是本任务可获取公开 PDF 的候选；可手动上传后重新发起。')
                doc = await import_paper(ImportPaper(record_id=paper['record_id'], paper_id=action.paper_id, mode='pdf'), request)
                run['readings'].append({'id': doc.id, 'title': doc.title})
                read_outcomes[action.paper_id] = {'status': 'ok', 'reading_id': doc.id, 'title': doc.title}
                event(tool, 'ok', '已获取并解析：' + doc.title)
            elif tool == 'compare':
                allowed = {doc['id'] for doc in run['readings']}
                if not set(action.args.reading_ids) <= allowed:
                    raise HTTPException(422, '只能对比本任务已获取或用户选定的 PDF。')
                run['comparison'] = await compare(action.args, request)
                event(tool, 'ok', '已生成带原文证据的对比结果。')
            elif tool == 'answer':
                if action.reading_id not in {doc['id'] for doc in run['readings']}:
                    raise HTTPException(422, '只能阅读本任务已获取或用户选定的 PDF。')
                run['answers'].append(await ask(action.reading_id, Question(question=action.question), request))
                event(tool, 'ok', '已完成单篇证据问答。')
        except HTTPException as exc:
            if tool == 'read':
                read_outcomes[action.paper_id] = {'status': 'failed', 'title': label, 'reason': str(exc.detail)}
            event(tool, 'error', (label + '：' if label else '') + str(exc.detail))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
            if tool == 'read':
                read_outcomes[action.paper_id] = {'status': 'failed', 'title': label, 'reason': '工具执行失败'}
            event(tool, 'error', '工具执行失败，未获得有效结果；可换用其他候选或结束。')
        return {}

    graph = StateGraph(State)
    graph.add_node('decide', decide)
    graph.add_node('execute', execute)
    graph.add_edge(START, 'decide')
    graph.add_conditional_edges('decide', lambda state: 'end' if state.get('done') else 'tool',
                                {'end': END, 'tool': 'execute'})
    graph.add_edge('execute', 'decide')
    try:
        run.update(status='running', message='正在执行研究任务。')
        persist()
        with tracing_context(enabled=False):
            async with asyncio.timeout(TOTAL_TIMEOUT):
                await graph.compile().ainvoke({'done': False}, {'recursion_limit': 24})
    except asyncio.CancelledError:
        run.update(status='cancelled', message='任务已取消，已获取的论文与结果仍保留。')
    except BudgetExceeded as exc:
        run.update(status='limited', message=str(exc))
    except TimeoutError:
        run.update(status='limited', message='任务超过 10 分钟，已停止并保留现有结果。')
    except Exception:
        run.update(status='failed', message='任务执行失败或模型返回了无效操作，已停止；已有结果仍保留。')
    finally:
        persist()
        current_budget.reset(token)


@router.get('')
def history(request: Request):
    return request.app.state.store.agent_history()


@router.get('/{identifier}')
def get_run(identifier: str, request: Request):
    run = request.app.state.store.agent_run(identifier)
    if not run:
        raise HTTPException(404, '任务不存在。')
    return run


@router.post('', status_code=202)
async def start(body: StartRun, request: Request):
    app = request.app
    if not app.state.model.configured or app.state.embedding.partial:
        raise HTTPException(503, '请配置回答模型，并补全或清空 Embedding 配置。')
    if any(not task.done() for task in app.state.agent_tasks.values()):
        raise HTTPException(409, '已有研究任务运行中，请等待完成或先取消。')
    docs = [app.state.store.reading(key) for key in dict.fromkeys(body.reading_ids)]
    if any(doc is None or doc.kind != 'pdf' for doc in docs):
        raise HTTPException(422, '所选记录必须是已保存的 PDF。')
    run = {'id': str(uuid4()), 'goal': body.goal, 'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'queued', 'message': '任务已创建。', 'steps': 0, 'events': [], 'papers': [],
        'search_ids': [], 'readings': [{'id': doc.id, 'title': doc.title} for doc in docs],
        'comparison': None, 'answers': [], 'usage': {}}
    app.state.store.save_agent_run(run)
    task = asyncio.create_task(execute_run(app, run))
    app.state.agent_tasks[run['id']] = task
    task.add_done_callback(lambda _: app.state.agent_tasks.pop(run['id'], None))
    return run


@router.post('/{identifier}/cancel')
async def cancel(identifier: str, request: Request):
    run = get_run(identifier, request)
    task = request.app.state.agent_tasks.get(identifier)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        run = get_run(identifier, request)
        if run['status'] in ('queued', 'running'):
            run.update(status='cancelled', message='任务已取消。')
            request.app.state.store.save_agent_run(run)
    return run
