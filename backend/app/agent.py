"""Bounded model-directed tool loop, with durable local task records."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal, TypedDict
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from pydantic import Field
from .agent_budget import Budget, BudgetExceeded, current_budget
from .tools import ToolExecutor, ACTION, action_schema, parse_action
from .tools.schemas import Strict

router = APIRouter(prefix='/api/agent/runs')
MAX_STEPS = 10
TOTAL_TIMEOUT = 600


class StartRun(Strict):
    goal: str = Field(min_length=2, max_length=3000)
    reading_ids: list[str] = Field(default_factory=list, max_length=5)
    mode: Literal['single', 'multi'] = 'single'


class State(TypedDict, total=False):
    action: dict
    done: bool


async def execute_run(app, run):
    store, model = app.state.store, app.state.model
    budget = Budget()
    token = current_budget.set(budget)
    counts = {'search': 0, 'download_pdf': 0, 'compare': 0, 'answer': 0}
    limits = {'search': 2, 'download_pdf': 5, 'compare': 1, 'answer': 2}
    attempted = set()
    rejected_in_a_row = 0

    def persist():
        run['usage'] = asdict(budget)
        store.save_agent_run(run)

    def event(tool, status, message):
        run['events'].append({'tool': tool, 'status': status, 'message': message,
                              'role': run.get('active_agent', 'single'),
                              'at': datetime.now(timezone.utc).isoformat()})
        persist()

    tool_executor = ToolExecutor(app, run, event)
    candidates = tool_executor.candidates
    read_outcomes = tool_executor.read_outcomes

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
        await tool_executor.execute(action)
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
