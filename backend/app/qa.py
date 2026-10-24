import asyncio
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .reading import get_reading, wait_operation
from .retrieval import retrieve

router = APIRouter(prefix='/api/readings')


class Question(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    question: str = Field(min_length=2, max_length=1500)


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class AnswerContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    supported: bool
    claims: list[Claim] = Field(max_length=6)
    reason: str = Field(max_length=1000)

    @model_validator(mode='after')
    def evidence_required(self):
        if self.supported and not self.claims or not self.supported and (self.claims or not self.reason.strip()):
            raise ValueError('inconsistent support')
        return self


@router.get('/{reading_id}/questions')
def history(reading_id: str, request: Request):
    get_reading(request, reading_id)
    return request.app.state.store.question_history(reading_id)


@router.post('/{reading_id}/questions')
async def ask(reading_id: str, body: Question, request: Request):
    doc = get_reading(request, reading_id)
    if doc.kind != 'pdf':
        raise HTTPException(422, '单篇问答需要上传或获取 PDF，只有来源摘要的记录暂不支持。')
    model, embedding = request.app.state.model, request.app.state.embedding
    if not model.configured:
        raise HTTPException(503, '请先配置回答问题使用的云端模型。')
    if embedding.partial:
        raise HTTPException(503, 'Embedding 配置不完整，请填写 BASE_URL、API_KEY 和 MODEL，或全部留空使用关键词检索。')

    async def work():
        selected, mode = await retrieve(doc, body.question, embedding, request.app.state.store,
            request.app.state.embedding_lock)
        if not selected:
            result = AnswerContent(supported=False, claims=[], reason='当前检索没有找到足够依据。请换用论文中的术语或配置多语言 Embedding 后重试；不能据此断定整篇论文未涉及该问题。')
        else:
            result = AnswerContent.model_validate(await model.answer_question(body.question, selected))
        sources = {item['id']: item for item in selected}
        claims = []
        for claim in result.claims:
            if any(key not in sources for key in claim.evidence_ids):
                raise ValueError('unknown evidence ID')
            claims.append({'text': claim.text, 'evidence': [sources[key] for key in dict.fromkeys(claim.evidence_ids)]})
        answer = {'id': str(uuid4()), 'reading_id': doc.id, 'question': body.question,
            'supported': result.supported, 'claims': claims,
            'reason': result.reason, 'retrieval': mode, 'model': model.name,
            'created_at': datetime.now(timezone.utc).isoformat()}
        request.app.state.store.save_question(answer)
        return answer
    try:
        return await wait_operation(request, work(), 240)
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(502, '检索或回答服务请求失败，请检查模型与 Embedding 配置后重试。没有自动重试或切换服务。') from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise HTTPException(502, '回答格式、证据编号或向量数据校验失败，未保存未经验证的回答。') from None
