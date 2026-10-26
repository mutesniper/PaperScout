"""Bounded model-directed tool loop, with durable local task records."""
import asyncio
import re
from copy import deepcopy
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
    # Full titles may include unindexed function words. Retry only a successful
    # empty response, not rate limits/network errors, with a bounded shorter query.
    terms = re.findall(r'[\w-]+', focused.queries[0], re.UNICODE)
    core = [term for term in terms if term.lower() not in {'a', 'an', 'the', 'at', 'of', 'in', 'on', 'for', 'to', 'and'}]
    if (not primary.papers and primary.sources and all(s.status == 'ok' for s in primary.sources)
            and len(core) >= 2 and len(core) < len(terms)):
        simplified = focused.model_copy(update={'queries': [' '.join(core)]})
        event('search', 'running', 'arXiv 返回空结果，去掉常见虚词后重试：' + simplified.queries[0])
        primary = await app.state.collect_search(SearchRequest(plan=simplified, sources=['arxiv'], limit=100))
    pool = deduplicate(primary.papers)
    statuses = primary.sources
    sources = ['arxiv']
    warnings = list(primary.warnings)
    public_count = sum(bool(p.fulltext_url and p.access in ('open_fulltext', 'open_preprint')) for p in pool)
    def title_key(text):
        return re.findall(r'[\w-]+', text.casefold(), re.UNICODE)
    exact_public = any(title_key(p.title) == title_key(focused.queries[0]) and p.fulltext_url
                       and p.access in ('open_fulltext', 'open_preprint') for p in pool)
    if (public_count < ARXIV_CANDIDATE_TARGET and not exact_public) or any(s.status != 'ok' for s in statuses):
        event('search', 'running', f'arXiv 提供 {public_count} 篇带公开全文链接的候选，或来源请求未完整成功；补充搜索 Semantic Scholar。')
        secondary = await app.state.collect_search(SearchRequest(plan=focused, sources=['semantic_scholar'], limit=100))
        pool = deduplicate(pool + secondary.papers)
        statuses = statuses + secondary.sources
        sources.append('semantic_scholar')
        warnings.extend(secondary.warnings)
    else:
        event('search', 'ok', ('arXiv 已找到标题匹配且带公开全文链接的论文' if exact_public else
              f'arXiv 已提供 {public_count} 篇带公开全文链接的候选') + '，跳过 Semantic Scholar。')
    return primary.model_copy(update={
        'request': SearchRequest(plan=focused, sources=sources, limit=10),
        'papers': rank_candidates(pool, 'relevance')[:10],
        'sources': statuses, 'candidate_count': len(pool), 'warnings': list(dict.fromkeys(warnings)),
        'ranking_note': 'Agent 优先 arXiv；空结果时最多简化虚词重试一次。标题匹配且有公开全文时跳过补充来源，否则公开全文候选不足 5 篇或来源失败时补充 Semantic Scholar。按 RRF 返回前 10 篇。',
    })


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class StartRun(Strict):
    goal: str = Field(min_length=2, max_length=3000)
    reading_ids: list[str] = Field(default_factory=list, max_length=5)
    mode: Literal['single', 'multi'] = 'single'


class Action(Strict):
    reason: str = Field(min_length=1, max_length=200)


class SearchAction(Action):
    tool: Literal['search']
    plan: Plan


class ReadAction(Action):
    """Download a search candidate, never an already imported PDF."""
    tool: Literal['download_pdf']
    paper_id: str = Field(description='Search candidate paper_id, NOT an available PDF reading_id.')


class CompareAction(Action):
    tool: Literal['compare']
    args: ComparisonRequest


class AnswerAction(Action):
    tool: Literal['answer']
    reading_id: str = Field(description='reading_id of an already imported PDF; no download required.')
    question: str = Field(min_length=2, max_length=1500)


class FinishAction(Action):
    tool: Literal['finish']


ACTION = TypeAdapter(Annotated[SearchAction | ReadAction | CompareAction | AnswerAction | FinishAction,
                               Field(discriminator='tool')])


def parse_action(raw, adapter=ACTION):
    # Accept older model responses, but never advertise the ambiguous name again.
    if isinstance(raw, dict) and raw.get('tool') == 'read':
        raw = {**raw, 'tool': 'download_pdf'}
    return adapter.validate_python(raw)


def action_schema(adapter, allowed):
    """Expose currently usable actions; runtime ID/budget checks remain authoritative."""
    schema = deepcopy(adapter.json_schema())
    mapping = schema['discriminator']['mapping']
    removed = {ref for tool, ref in mapping.items() if tool not in allowed}
    schema['discriminator']['mapping'] = {tool: ref for tool, ref in mapping.items() if tool in allowed}
    schema['oneOf'] = [option for option in schema['oneOf'] if option['$ref'] not in removed]
    for ref in removed:
        schema['$defs'].pop(ref.rsplit('/', 1)[-1], None)
    return schema


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
    counts = {'search': 0, 'download_pdf': 0, 'compare': 0, 'answer': 0}
    limits = {'search': 2, 'download_pdf': 5, 'compare': 1, 'answer': 2}
    attempted = set()
    candidates = {}
    rejected_in_a_row = 0
    read_outcomes = {}

    def persist():
        run['usage'] = asdict(budget)
        store.save_agent_run(run)

    def event(tool, status, message):
        run['events'].append({'tool': tool, 'status': status, 'message': message,
                              'role': run.get('active_agent', 'single'),
                              'at': datetime.now(timezone.utc).isoformat()})
        persist()

    def context():
        downloadable = [p['paper_id'] for p in candidates.values()
                        if p['public_pdf'] and p['paper_id'] not in read_outcomes]
        allowed = ['finish']
        if counts['search'] < limits['search']:
            allowed.append('search')
        if downloadable and counts['download_pdf'] < limits['download_pdf']:
            allowed.append('download_pdf')
        if run['readings'] and counts['answer'] < limits['answer']:
            allowed.append('answer')
        if len(run['readings']) >= 2 and counts['compare'] < limits['compare']:
            allowed.append('compare')
        return {'date': datetime.now(timezone.utc).date().isoformat(), 'goal': run['goal'],
            'available_pdfs': [{'reading_id': d['id'], 'title': d['title'], 'status': 'ready_for_analysis'}
                               for d in run['readings']],
            'allowed_actions': allowed, 'downloadable_candidate_ids': downloadable,
            'candidates': list(candidates.values())[:20],
            'events': run['events'][-16:], 'remaining': {key: limits[key] - counts[key] for key in limits},
            'attempted_actions': sorted(attempted), 'read_outcomes': dict(read_outcomes),
            'has_comparison': bool(run['comparison']), 'answered_questions': [a['question'] for a in run['answers']]}

    def finish(reason):
        # Research conclusions are rendered only from checked tool results.
        partial = any(e['status'] in ('error', 'partial') for e in run['events']) or not (run['papers'] or run['comparison'] or run['answers'])
        run.update(status='partial' if partial else 'completed',
                   message='任务已结束，请查看实际获取的论文、证据结果及执行记录。' if not partial else
                   '任务已结束，但部分操作失败或未获得结果；请查看缺失说明，可上传 PDF 后重试。')
        event('finish', 'ok', reason)
        return {'done': True}

    async def decide(state):
        if state.get('done'):
            return {'done': True}
        if run['steps'] >= MAX_STEPS:
            run.update(status='limited', message='已达到执行步数上限，保留已有结果。')
            return {'done': True}
        run['steps'] += 1
        event('agent', 'running', '正在选择下一步操作。')
        data = context()
        action = parse_action(await model.agent_decide(data, action_schema(ACTION, data['allowed_actions'])))
        if action.tool == 'search':
            action.plan = action.plan.model_copy(update={'queries': action.plan.queries[:1]})
        if action.tool == 'finish':
            return finish(action.reason)
        return {'action': action.model_dump(mode='json'), 'done': False}

    async def execute(state):
        nonlocal rejected_in_a_row
        action = parse_action(state['action'])
        tool = action.tool
        if tool == 'download_pdf' and action.paper_id in {d['id'] for d in run['readings']}:
            rejected_in_a_row += 1
            event(tool, 'corrected', '该 ID 是已入库且可直接分析的 PDF，不需要下载。'
                  '请使用 answer.reading_id 提问，或 compare.args.reading_ids 对比；这不是文件读取失败。')
            if rejected_in_a_row >= 2:
                run.update(status='limited', message='连续把已有 PDF 用于下载，已停止无效操作并保留已有结果。')
                return {'done': True}
            return {}
        signature = action.model_dump_json(exclude={'reason'})
        if signature in attempted or counts[tool] >= limits[tool]:
            rejected_in_a_row += 1
            target = candidates.get(action.paper_id, {}).get('title', action.paper_id) if tool == 'download_pdf' else ''
            event(tool, 'error', (target + '：' if target else '') + '该操作已执行过或已达到调用上限，请选择其他操作或结束。')
            if rejected_in_a_row >= 2:
                run.update(status='limited', message='模型连续重复无效操作，已提前停止，避免继续消耗调用额度。请查看具体失败原因。')
                return {'done': True}
            return {}
        rejected_in_a_row = 0
        attempted.add(signature)
        counts[tool] += 1  # Failed calls also consume the allowance.
        label = candidates.get(action.paper_id, {}).get('title', action.paper_id) if tool == 'download_pdf' else ''
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
                failures = [source.source + '：' + source.message for source in result.sources if source.status != 'ok']
                empty = [source.source + '：检索成功但无匹配论文' for source in result.sources
                         if source.status == 'ok' and source.count == 0]
                event(tool, 'ok' if not failures else 'partial',
                    f'获取 {len(result.papers)} 篇候选。' + '；'.join(empty + failures))
            elif tool == 'download_pdf':
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
            if tool == 'download_pdf':
                read_outcomes[action.paper_id] = {'status': 'failed', 'title': label, 'reason': str(exc.detail)}
            event(tool, 'error', (label + '：' if label else '') + str(exc.detail))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
            if tool == 'download_pdf':
                read_outcomes[action.paper_id] = {'status': 'failed', 'title': label, 'reason': '工具执行失败'}
            event(tool, 'error', '工具执行失败，未获得有效结果；可换用其他候选或结束。')
        return {}

    if run.get('mode', 'single') == 'multi':
        from .multi_agent import build_graph
        graph = build_graph(model, run, context, event, execute, finish)
    else:
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
                await graph.compile().ainvoke({'done': False}, {'recursion_limit': 40})
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
        'mode': body.mode,
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
