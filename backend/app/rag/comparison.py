"""对数量受限的本地 PDF 进行有原文证据支撑的比较。"""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
import re
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.papers.service import get_reading, wait_operation
from app.rag.retrieval import retrieve, chunks_for, readable_evidence

router = APIRouter(prefix='/api/comparisons')
Dimension = Literal['goal', 'method', 'experiments', 'results', 'limitations']
DIMENSIONS = {
    'goal': ('研究目标', 'research problem objective motivation contribution'),
    'method': ('方法', 'method approach framework algorithm'),
    'experiments': ('实验设置', 'experiment dataset benchmark evaluation setup metric'),
    'results': ('实验结果', 'results performance accuracy evaluation'),
    'limitations': ('局限', 'limitations weaknesses future work'),
}


def comparison_chunks(doc, dimension, ranked):
    """研究目标优先使用章节结构对应的证据，不依赖偶然出现的 research 一词。"""
    back_matter = re.compile(r'^(?:\d+[.\s]*)?(?:references|bibliography|author biographies|biographies|参考文献|作者简介)\b', re.I)
    eligible = readable_evidence(doc, [chunk for chunk in chunks_for(doc) if not back_matter.search(chunk['section'].strip())])
    allowed = {chunk['id'] for chunk in eligible}
    ranked = [chunk for chunk in ranked if chunk['id'] in allowed]
    if dimension != 'goal':
        return ranked[:2]
    # PDF 章节解析失败时，标题可能仍包含在页面文本中。
    abstract = re.compile(r'\babstract\b|摘要', re.I)
    for i, chunk in enumerate(eligible):
        if chunk['page'] <= 3 and (abstract.search(chunk['section']) or abstract.search(chunk['text'])):
            selected = [chunk]
            if i + 1 < len(eligible):
                following = eligible[i + 1]
                if following['section_id'] == chunk['section_id'] and following['page'] <= chunk['page'] + 1:
                    selected.append(following)
            selected.extend(c for c in ranked if c['id'] not in {x['id'] for x in selected})
            return selected[:2]
    introduction = [chunk for chunk in eligible if chunk['page'] <= 3 and
                    re.search(r'\bintroduction\b|引言|绪论', chunk['section'], re.I)]
    selected = introduction[:2]
    selected.extend(c for c in ranked if c['id'] not in {x['id'] for x in selected})
    return selected[:2]


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reading_ids: list[str] = Field(min_length=2, max_length=5)
    dimensions: list[Dimension] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.reading_ids)) != len(self.reading_ids) or len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError('duplicate selections')
        return self


class Cell(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reading_id: str
    dimension: Dimension
    supported: bool
    text: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(max_length=2)


class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cells: list[Cell] = Field(max_length=25)


@router.get('')
def history(request: Request):
    return request.app.state.store.comparison_history()


@router.post('')
async def compare(body: ComparisonRequest, request: Request):
    docs = [get_reading(request, key) for key in body.reading_ids]
    if any(doc.kind != 'pdf' for doc in docs):
        raise HTTPException(422, '请选择 2～5 篇已上传或获取全文的 PDF。')
    model, embedding = request.app.state.model, request.app.state.embedding
    if not model.configured or embedding.partial:
        raise HTTPException(503, '请配置回答模型，并补全或清空 Embedding 的三个配置项。')

    async def work():
        material, sources = [], {}
        modes = set()
        for doc in docs:
            for dimension in body.dimensions:
                label, query = DIMENSIONS[dimension]
                # 检索后已优先排列用于研究目标的结构性证据。
                # 优先选用这些证据，避免为了读取摘要而索引整篇综述。
                chunks = comparison_chunks(doc, dimension, []) if dimension == 'goal' else []
                if chunks:
                    modes.add('structural')
                else:
                    ranked, mode = await retrieve(doc, label + ' ' + query, embedding,
                        request.app.state.store, request.app.state.embedding_lock)
                    chunks = comparison_chunks(doc, dimension, ranked)
                    modes.add(mode)
                excerpts = [{**chunk, 'id': doc.id + ':' + chunk['id'], 'reading_id': doc.id}
                            for chunk in chunks]
                sources[(doc.id, dimension)] = {chunk['id']: chunk for chunk in excerpts}
                material.append({'reading_id': doc.id, 'title': doc.title,
                    'dimension': dimension, 'label': label, 'excerpts': excerpts})
        # 无检索结果的位置已确定，无需请求模型补全。
        available = [item for item in material if item['excerpts']]
        result = Output.model_validate(await model.compare_papers(available)) if available else Output(cells=[])
        expected = {(item['reading_id'], item['dimension']) for item in available}
        keys = [(cell.reading_id, cell.dimension) for cell in result.cells]
        if len(keys) != len(set(keys)) or set(keys) != expected:
            raise ValueError('missing, duplicate or foreign comparison cell')
        resolved = {}
        for cell in result.cells:
            catalog = sources[(cell.reading_id, cell.dimension)]
            if cell.supported != bool(cell.evidence_ids) or any(key not in catalog for key in cell.evidence_ids):
                raise ValueError('invalid evidence scope')
            resolved[(cell.reading_id, cell.dimension)] = {
                'reading_id': cell.reading_id, 'dimension': cell.dimension,
                'supported': cell.supported,
                'text': cell.text if cell.supported else '未找到足够信息，不能据此断定论文未涉及该内容。',
                'evidence': [catalog[key] for key in dict.fromkeys(cell.evidence_ids)]}
        cells = [resolved.get((item['reading_id'], item['dimension']), {
            'reading_id': item['reading_id'], 'dimension': item['dimension'], 'supported': False,
            'text': '未找到相关信息，请核对原论文。', 'evidence': []}) for item in material]
        answer = {'id': str(uuid4()), 'created_at': datetime.now(timezone.utc).isoformat(),
            'papers': [{'id': doc.id, 'title': doc.title, 'source_url': doc.source_url} for doc in docs],
            'dimensions': body.dimensions, 'cells': cells,
            'retrieval': '+'.join(sorted(modes)), 'model': model.name}
        request.app.state.store.save_comparison(answer)
        return answer

    try:
        return await wait_operation(request, work(), 600)
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(502, '对比服务请求失败或超时，未保存部分结果。请稍后重试。') from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise HTTPException(502, '对比内容或证据校验失败，未保存结果。') from None
