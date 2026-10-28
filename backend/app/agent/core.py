"""Bounded model-directed tool loop, with durable local task records."""
import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal, TypedDict
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from pydantic import Field, model_validator
from app.agent.budget import Budget, BudgetExceeded, current_budget
from app.tools import ToolExecutor, ACTION, action_schema, parse_action
from app.tools.schemas import Strict
from app.agent.local_router import RouterError, Clarification, Completion, routing_state

router = APIRouter(prefix='/api/agent/runs')
MAX_STEPS = 10
TOTAL_TIMEOUT = 600


class StartRun(Strict):
    goal: str = Field(min_length=2, max_length=3000)
    reading_ids: list[str] = Field(default_factory=list, max_length=5)
    mode: Literal['single', 'multi'] = 'single'
    router_mode: Literal['cloud', 'local'] = 'cloud'

    @model_validator(mode='after')
    def local_scope(self):
        if self.router_mode == 'local' and (self.mode != 'single' or len(self.goal) > 1500):
            raise ValueError('本地路由仅支持单 Agent，目标最多1500字。')
        return self


class Reply(Strict):
    text: str = Field(min_length=1, max_length=1000)


class State(TypedDict, total=False):
    action: dict
    done: bool


async def execute_run(app, run):
    store, model = app.state.store, app.state.model
    budget = Budget(**{k:v for k,v in run.get('usage', {}).items() if k in Budget.__dataclass_fields__})
    token = current_budget.set(budget)
    saved = run.get('router_checkpoint', {})
    counts = dict(saved.get('counts', {'search': 0, 'download_pdf': 0, 'compare': 0, 'answer': 0}))
    limits = {'search': 2, 'download_pdf': 5, 'compare': 1, 'answer': 2}
    attempted = set(saved.get('attempted', []))
    rejected_in_a_row = 0
    started_at = time.monotonic()

    def persist(final=False):
        if not final and run['status'] not in ('queued','running'):
            return  # Publish terminal/waiting state together with checkpoint and elapsed time.
        run['usage'] = asdict(budget)
        run['router_checkpoint'] = {'counts':dict(counts), 'attempted':sorted(attempted),
            'candidates':dict(candidates), 'read_outcomes':dict(read_outcomes)}
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
            'has_comparison': bool(run['comparison']), 'answered_questions': [a['question'] for a in run['answers']],
            'user_replies':run.get('user_replies', [])}

    def finish(reason, completed=None):
        # Research conclusions are rendered only from checked tool results.
        partial = completed is False or any(e['status'] in ('error', 'partial') for e in run['events']) or not (run['papers'] or run['comparison'] or run['answers'])
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
        if run.get('router_mode', 'cloud') == 'local':
            local_state = routing_state(run,candidates,read_outcomes,counts,limits,MAX_STEPS-run['steps']+1)
            budget.local_routes += 1
            route = await app.state.local_router.decide(local_state)
            # Validate again here even if the service/client implementation is replaced.
            from app.agent.router_contract import allowed_routes
            if route not in allowed_routes(local_state):
                raise RouterError('小模型选择的操作不满足资源或预算条件，已拦截。')
            event('router', 'ok', '本地微调路由选择：'+route)
            data['selected_route'] = route
            data['allowed_actions'] = [route]
            data['results'] = local_state.results
            if route == 'clarify':
                if run.get('clarification_count', 0) >= 2:
                    raise RouterError('已达到两次澄清上限，任务停止；请整理目标后重新发起。')
                question = Clarification.model_validate(await model.agent_parameters(route,data,Clarification.model_json_schema()))
                run.update(status='needs_input',message='请补充信息后继续。',clarification=question.question,
                           clarification_count=run.get('clarification_count',0)+1)
                event('clarify','waiting',question.question)
                return {'done':True}
            if route == 'finish':
                completion = Completion.model_validate(await model.agent_parameters(route,data,Completion.model_json_schema()))
                return finish(completion.reason,completion.completed)
            action = parse_action(await model.agent_parameters(route,data,action_schema(ACTION,[route])))
            if action.tool != route:
                raise RouterError('云端参数生成与小模型指定工具不一致，已拦截，未执行替代工具。')
            if route == 'download_pdf' and action.paper_id not in data['downloadable_candidate_ids']:
                raise RouterError('下载参数不是可用候选或已经尝试过，已拦截。')
        else:
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
        if tool != 'download_pdf':
            last = run['events'][-1]
            run.setdefault('tool_outcomes', []).append({'operation':tool,
                'status':'failed' if last['status']=='error' else 'success', 'detail':last['message']})
        return {}

    if run.get('mode', 'single') == 'multi':
        from app.agent.multi import build_graph
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
            async with asyncio.timeout(max(.001, TOTAL_TIMEOUT-run.get('active_seconds',0))):
                await graph.compile().ainvoke({'done': False}, {'recursion_limit': 40})
    except asyncio.CancelledError:
        run.update(status='cancelled', message='任务已取消，已获取的论文与结果仍保留。')
    except BudgetExceeded as exc:
        run.update(status='limited', message=str(exc))
    except RouterError as exc:
        run.update(status='failed', message=str(exc))
        event('router','error',str(exc))
    except TimeoutError:
        run.update(status='limited', message='任务超过 10 分钟，已停止并保留现有结果。')
    except Exception:
        run.update(status='failed', message='任务执行失败或模型返回了无效操作，已停止；已有结果仍保留。')
    finally:
        run['active_seconds'] = run.get('active_seconds',0)+time.monotonic()-started_at
        persist(final=True)
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
        'mode': body.mode, 'router_mode': body.router_mode,
        'router_model': 'paperscout-router-v1.0.0' if body.router_mode == 'local' else app.state.model.name,
        'status': 'queued', 'message': '任务已创建。', 'steps': 0, 'events': [], 'papers': [],
        'search_ids': [], 'readings': [{'id': doc.id, 'title': doc.title, 'source_url':doc.source_url} for doc in docs],
        'comparison': None, 'answers': [], 'usage': {}}
    app.state.store.save_agent_run(run)
    task = asyncio.create_task(execute_run(app, run))
    app.state.agent_tasks[run['id']] = task
    task.add_done_callback(lambda _: app.state.agent_tasks.pop(run['id'], None))
    return run


@router.post('/{identifier}/reply', status_code=202)
async def reply(identifier: str, body: Reply, request: Request):
    app = request.app
    run = get_run(identifier,request)
    if run.get('router_mode') != 'local' or run['status'] != 'needs_input':
        raise HTTPException(409,'该任务当前不在等待补充信息。')
    if any(not task.done() for task in app.state.agent_tasks.values()):
        raise HTTPException(409,'已有研究任务运行中，请稍后再试。')
    if not app.state.model.configured or app.state.embedding.partial:
        raise HTTPException(503,'请配置回答模型，并补全或清空 Embedding 配置。')
    run.setdefault('user_replies', []).append('用户补充：'+body.text)
    run.update(status='queued',message='已收到补充信息，继续原任务。',clarification=None)
    app.state.store.save_agent_run(run)
    task = asyncio.create_task(execute_run(app,run))
    app.state.agent_tasks[run['id']] = task
    task.add_done_callback(lambda _: app.state.agent_tasks.pop(run['id'],None))
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
    elif run['status'] == 'needs_input':
        run.update(status='cancelled',message='任务已取消。',clarification=None)
        request.app.state.store.save_agent_run(run)
    return run
