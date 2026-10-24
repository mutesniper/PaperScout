"""Run from the repository root: python backend/evaluation/run.py."""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.models import CloudModel
from app.qa import Question, ask
from app.reading_schemas import Reading
from app.retrieval import CloudEmbedding, chunks_for, retrieve
from app.storage import Store


class Gold(BaseModel):
    page: int = Field(ge=1)
    quote: str = Field(min_length=10)


class Case(BaseModel):
    id: str
    reading_id: str
    question: str = Field(min_length=2, max_length=1500)
    answerable: bool
    reference_answer: str = Field(min_length=1)
    evidence: list[Gold]
    tags: list[str] = Field(default_factory=list)


class Dataset(BaseModel):
    version: str
    description: str
    documents: list[Reading] = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_references(self):
        docs = {d.id: d for d in self.documents}
        if len(docs) != len(self.documents) or len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError('duplicate document or case ID')
        for case in self.cases:
            if case.reading_id not in docs or bool(case.evidence) != case.answerable:
                raise ValueError('invalid case document or evidence')
            chunks = chunks_for(docs[case.reading_id])
            for gold in case.evidence:
                if not any(matches(c, gold) for c in chunks):
                    raise ValueError('gold quote must occur in a chunk on the annotated page')
        return self


def matches(chunk, gold):
    return chunk['page'] == gold.page and ' '.join(gold.quote.split()) in chunk['text']


def retrieval_metrics(selected, case):
    if not case.answerable:
        return {'hit_at_6': None, 'evidence_recall_at_6': None, 'reciprocal_rank': None}
    hits = [any(matches(c, gold) for c in selected) for gold in case.evidence]
    first = next((rank for rank, c in enumerate(selected, 1)
                  if any(matches(c, gold) for gold in case.evidence)), None)
    return {'hit_at_6': int(any(hits)), 'evidence_recall_at_6': sum(hits) / len(hits),
            'reciprocal_rank': 1 / first if first else 0}


class Meter:
    """Only provider-reported usage. Never store URLs, headers or credentials."""
    def __init__(self):
        self.calls = []
        self.pending = {}

    async def request(self, request):
        row = {'kind': 'embedding' if request.url.path.endswith('/embeddings') else 'generation',
               'status': None, 'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None}
        self.calls.append(row)
        self.pending[id(request)] = row

    async def response(self, response):
        await response.aread()
        try:
            usage = response.json().get('usage') or {}
        except (ValueError, AttributeError):
            usage = {}
        if not isinstance(usage, dict):
            usage = {}
        numeric = lambda v: v if type(v) is int and v >= 0 else None
        row = self.pending.pop(id(response.request), None)
        if row is None:
            await self.request(response.request)
            row = self.pending.pop(id(response.request))
        row.update(status=response.status_code,
            **{key: numeric(usage.get(key)) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')})

    def totals(self):
        # Missing usage is unknown, not zero; no double counting reasoning subfields.
        return {kind: {'requests': len(rows), 'total_tokens':
            sum(r['total_tokens'] for r in rows) if all(r['total_tokens'] is not None for r in rows) else None}
            for kind in ('generation', 'embedding')
            for rows in [[r for r in self.calls if r['kind'] == kind]]}


class ObservedModel:
    def __init__(self, model):
        self.model, self.selected = model, []
        self.configured, self.name = model.configured, model.name

    async def answer_question(self, question, chunks):
        self.selected = chunks
        return await self.model.answer_question(question, chunks)


class Request:
    def __init__(self, state):
        self.app = SimpleNamespace(state=state)

    async def is_disconnected(self):
        return False


def average(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return sum(values) / len(values) if values else None


async def evaluate(dataset, *, live=False, semantic=False, transport=None):
    meter = Meter()
    rows = []
    with tempfile.TemporaryDirectory(prefix='paperscout-eval-') as directory:
        store = Store(Path(directory) / 'evaluation.db')
        for doc in dataset.documents:
            store.save_reading(doc)
        async with httpx.AsyncClient(transport=transport, event_hooks={'request': [meter.request], 'response': [meter.response]}) as client:
            model = CloudModel(client, *(os.getenv(k, '') if live else '' for k in
                ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL')))
            embedding = CloudEmbedding(client, *(os.getenv(k, '') if semantic else '' for k in
                ('EMBEDDING_BASE_URL', 'EMBEDDING_API_KEY', 'EMBEDDING_MODEL')))
            if live and not model.configured:
                raise ValueError('live 模式需要完整配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL')
            if semantic and not embedding.configured:
                raise ValueError('semantic 模式需要完整配置三个 EMBEDDING 环境变量')
            observed = ObservedModel(model)
            state = SimpleNamespace(store=store, model=observed, embedding=embedding, embedding_lock=asyncio.Lock())
            docs = {d.id: d for d in dataset.documents}
            for case in dataset.cases:
                start, call_start = time.perf_counter(), len(meter.calls)
                row = {'id': case.id, 'question': case.question, 'reading_id': case.reading_id,
                       'expected_answerable': case.answerable, 'reference_answer': case.reference_answer,
                       'gold_evidence': [g.model_dump() for g in case.evidence], 'tags': case.tags,
                       'manual_review': {'answer_correct': None, 'claims_supported_by_evidence': None, 'notes': ''}}
                observed.selected = []
                try:
                    if live:
                        answer = await ask(case.reading_id, Question(question=case.question), Request(state))
                        selected = observed.selected
                        row['answer'] = answer
                        row['support_decision_correct'] = int(answer['supported'] == case.answerable)
                        row['refusal_correct'] = int(not answer['supported']) if not case.answerable else None
                        # This checks provenance only, NOT semantic entailment.
                        cited = [e for claim in answer['claims'] for e in claim['evidence']]
                        row['citation_provenance_valid'] = all(e in selected for e in cited) if cited else None
                    else:
                        selected, _ = await retrieve(docs[case.reading_id], case.question, embedding, store, state.embedding_lock)
                    row.update(retrieval_metrics(selected, case))
                    row['retrieved'] = selected
                    row['status'] = 'ok'
                except Exception as exc:
                    # Include failed answerable cases as zero retrieval scores, never omit failures from totals.
                    row.update(retrieval_metrics([], case))
                    row.update(status='error', error_type=type(exc).__name__)
                    if live:
                        row['support_decision_correct'] = 0
                        row['refusal_correct'] = 0 if not case.answerable else None
                row['seconds'] = round(time.perf_counter() - start, 4)
                row['calls'] = meter.calls[call_start:]
                rows.append(row)
    return {'dataset_version': dataset.version, 'description': dataset.description,
        'mode': 'live' if live else 'retrieval', 'retrieval': 'semantic' if semantic else 'keyword',
        'model': model.name or None, 'embedding_model': embedding.name or None,
        'cache_policy': 'isolated cold cache per run; reused between questions of the same document',
        'created_at': datetime.now(timezone.utc).isoformat(), 'usage': meter.totals(),
        'summary': {'cases': len(rows), 'errors': sum(r['status'] == 'error' for r in rows),
            **{key: average(rows, key) for key in ('hit_at_6', 'evidence_recall_at_6', 'reciprocal_rank',
                'support_decision_correct', 'refusal_correct', 'seconds')}}, 'cases': rows}


def markdown(report):
    lines = ['# PaperScout 固定样本评测', '',
        '这不是完整论文研究能力评分。自动指标不证明回答语义正确；人工审核项留空待审。', '',
        f"模式：{report['mode']} / {report['retrieval']}；数据版本：{report['dataset_version']}", '',
        '```json', json.dumps(report['summary'], ensure_ascii=False, indent=2), '```', '',
        '用量（null 表示服务未返回，generation 与 embedding 分开统计）：', '',
        '```json', json.dumps(report['usage'], indent=2), '```']
    for row in report['cases']:
        lines += ['', f"## {row['id']}", '', row['question'], '',
            f"参考答案：{row['reference_answer']}", '', f"状态：{row['status']}；耗时：{row['seconds']} 秒", '',
            '```json', json.dumps({k: row.get(k) for k in ('answer', 'gold_evidence', 'retrieved', 'calls')},
                                  ensure_ascii=False, indent=2), '```', '',
            '- [ ] 回答内容正确（对照参考答案，允许合理改写）',
            '- [ ] 每条结论受到引用原文支持', '- [ ] 无证据的问题没有编造答案']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path(__file__).parent / 'fixtures.json')
    parser.add_argument('--live', action='store_true', help='发送检索片段到配置的大模型，可能产生费用')
    parser.add_argument('--semantic', action='store_true', help='调用配置的 Embedding 服务，可能产生费用')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/evaluation')
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    dataset = Dataset.model_validate_json(raw)
    if args.live or args.semantic:
        load_dotenv(ROOT / '.env')
    report = asyncio.run(evaluate(dataset, live=args.live, semantic=args.semantic))
    report['dataset_sha256'] = hashlib.sha256(raw).hexdigest()
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    report['git_commit'] = revision.stdout.strip() if revision.returncode == 0 else None
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True)
    report['working_tree_dirty'] = bool(status.stdout.strip()) if status.returncode == 0 else None
    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = args.output / stamp
    path.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    path.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    print(f'报告：{path}.json / .md')
    return 1 if report['summary']['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
