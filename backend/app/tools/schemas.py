"""单智能体与多智能体选择工具时共用的动作契约。"""
from copy import deepcopy
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from app.schemas import Plan
from app.rag.comparison import ComparisonRequest

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


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
    # 兼容旧版模型响应，但不再向模型提供有歧义的旧名称。
    if isinstance(raw, dict) and raw.get('tool') == 'read':
        raw = {**raw, 'tool': 'download_pdf'}
    return adapter.validate_python(raw)


def action_schema(adapter, allowed):
    """提供当前可用的动作，最终仍以运行时的 ID 和预算校验为准。"""
    schema = deepcopy(adapter.json_schema())
    mapping = schema['discriminator']['mapping']
    removed = {ref for tool, ref in mapping.items() if tool not in allowed}
    schema['discriminator']['mapping'] = {tool: ref for tool, ref in mapping.items() if tool in allowed}
    schema['oneOf'] = [option for option in schema['oneOf'] if option['$ref'] not in removed]
    for ref in removed:
        schema['$defs'].pop(ref.rsplit('/', 1)[-1], None)
    return schema


