"""Supervisor and two scoped specialists; tool execution and budgets stay shared."""
from typing import Annotated, Literal, TypedDict
from pydantic import Field, TypeAdapter
from langgraph.graph import StateGraph, START, END
from .agent import Action, SearchAction, ReadAction, CompareAction, AnswerAction, FinishAction, action_schema, parse_action

MAX_DECISIONS = 18


class Delegate(Action):
    tool: Literal['delegate']
    role: Literal['retrieval', 'analysis']
    task: str = Field(min_length=2, max_length=600)


class Report(Action):
    tool: Literal['report']


COORDINATOR = TypeAdapter(Annotated[Delegate | FinishAction, Field(discriminator='tool')])
RETRIEVAL = TypeAdapter(Annotated[SearchAction | ReadAction | Report, Field(discriminator='tool')])
ANALYSIS = TypeAdapter(Annotated[CompareAction | AnswerAction | Report, Field(discriminator='tool')])
LABELS = {'coordinator': '协调 Agent', 'retrieval': '检索 Agent', 'analysis': '分析 Agent'}


class TeamState(TypedDict, total=False):
    role: str
    task: str
    action: dict
    report: bool
    done: bool


def build_graph(model, run, context, event, execute, finish):
    no_progress = 0

    def begin(role):
        run['active_agent'] = role
        if run['steps'] >= MAX_DECISIONS:
            run.update(status='limited', message='多 Agent 已达到总决策步数上限，保留已有结果。')
            return False
        run['steps'] += 1
        event('agent', 'running', LABELS[role] + '正在选择下一步操作。')
        return True

    def shared():
        data = context()
        # Only brief, validated outputs are handed off, never full PDFs or model transcripts.
        data['answer_results'] = [{'reading_id': a['reading_id'], 'question': a['question'],
            'supported': a['supported'], 'reason': a['reason'],
            'claims': [c['text'][:400] for c in a['claims']]} for a in run['answers']]
        comparison = run['comparison']
        data['comparison_results'] = [{'reading_id': c['reading_id'], 'dimension': c['dimension'],
            'supported': c['supported'], 'text': c['text'][:400]} for c in comparison['cells']] if comparison else []
        data['remaining_decisions'] = MAX_DECISIONS - run['steps']
        data['budget'] = run['usage']
        return data

    async def coordinator(state):
        if state.get('done') or not begin('coordinator'):
            return {'done': True}
        action = COORDINATOR.validate_python(await model.agent_role_decide(
            'coordinator', shared(), COORDINATOR.json_schema()))
        if action.tool == 'finish':
            return finish(action.reason)
        event('delegate', 'running', f'交给{LABELS[action.role]}：{action.task}')
        return {'role': action.role, 'task': action.task, 'done': False, 'report': False}

    async def specialist(state):
        nonlocal no_progress
        role = state['role']
        if not begin(role):
            return {'done': True}
        adapter = RETRIEVAL if role == 'retrieval' else ANALYSIS
        data = shared()
        data['assigned_task'] = state['task']
        schema = action_schema(adapter, [*data['allowed_actions'], 'report'])
        action = parse_action(await model.agent_role_decide(role, data, schema), adapter)
        if action.tool == 'report':
            no_progress += 1
            event('report', 'partial', action.reason)
            if no_progress >= 2:
                run.update(status='limited', message='角色连续未执行有效操作，已停止往返并保留已有结果。')
                return {'done': True}
            return {'report': True}
        no_progress = 0
        if action.tool == 'search':
            action.plan = action.plan.model_copy(update={'queries': action.plan.queries[:1]})
        return {'action': action.model_dump(mode='json'), 'report': False}

    graph = StateGraph(TeamState)
    graph.add_node('coordinator', coordinator)
    graph.add_node('retrieval', specialist)
    graph.add_node('analysis', specialist)
    graph.add_node('execute', execute)
    graph.add_edge(START, 'coordinator')
    graph.add_conditional_edges('coordinator', lambda s: END if s.get('done') else s['role'],
        {END: END, 'retrieval': 'retrieval', 'analysis': 'analysis'})
    for role in ('retrieval', 'analysis'):
        graph.add_conditional_edges(role,
            lambda s: END if s.get('done') else 'coordinator' if s.get('report') else 'execute',
            {END: END, 'coordinator': 'coordinator', 'execute': 'execute'})
    graph.add_conditional_edges('execute', lambda s: END if s.get('done') else 'coordinator',
        {END: END, 'coordinator': 'coordinator'})
    return graph
