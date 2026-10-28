import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from evaluation.run import Dataset, Meter, evaluate, retrieval_metrics


def dataset():
    return Dataset.model_validate_json((Path(__file__).parents[1] / 'evaluation/fixtures.json').read_bytes())


def test_metrics_require_page_and_quote_and_penalize_missing_evidence():
    case = dataset().cases[0]
    gold = case.evidence[0]
    assert retrieval_metrics([{'page': 99, 'text': gold.quote}], case)['hit_at_6'] == 0
    chunks = [{'page': 1, 'text': 'unrelated'}, {'page': gold.page, 'text': gold.quote}]
    assert retrieval_metrics(chunks, case)['reciprocal_rank'] == .5
    assert retrieval_metrics([], dataset().cases[3])['hit_at_6'] is None


def test_invalid_gold_fails_before_any_calls():
    data = dataset().model_dump()
    data['cases'][0]['evidence'][0]['quote'] = 'fabricated reference evidence'
    with pytest.raises(ValidationError):
        Dataset.model_validate(data)


def test_offline_never_calls_network_even_with_environment_configured(monkeypatch):
    for key in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL', 'EMBEDDING_BASE_URL', 'EMBEDDING_API_KEY', 'EMBEDDING_MODEL'):
        monkeypatch.setenv(key, 'configured')
    def forbidden(request):
        raise AssertionError('offline run attempted network')
    result = asyncio.run(evaluate(dataset(), transport=httpx.MockTransport(forbidden)))
    assert result['summary']['cases'] == 9
    assert result['summary']['errors'] == 0
    assert result['summary']['hit_at_6'] == 1
    assert result['summary']['support_decision_correct'] is None
    assert result['usage']['generation']['requests'] == 0
    assert all(row['manual_review']['answer_correct'] is None for row in result['cases'])


def test_live_uses_production_qa_validation_and_keeps_failures(monkeypatch):
    for key, value in [('LLM_BASE_URL', 'https://test/v1'), ('LLM_API_KEY', 'fake'), ('LLM_MODEL', 'test')]:
        monkeypatch.setenv(key, value)
    data = dataset()
    data.cases = [data.cases[0], data.cases[3]]
    def handler(request):
        question = json.loads(json.loads(request.content)['messages'][1]['content'])['question']
        # 真实 ask() 实现必须拒绝编造的引用。
        answer = {'supported': True, 'claims': [{'text': 'bad', 'evidence_ids': ['c999']}], 'reason': ''} if '方法' in question else {
            'supported': False, 'claims': [], 'reason': '未报告费用。'}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}})
    result = asyncio.run(evaluate(data, live=True, transport=httpx.MockTransport(handler)))
    assert result['summary']['errors'] == 1
    assert result['summary']['support_decision_correct'] == .5
    assert result['summary']['refusal_correct'] == 1
    assert result['usage']['generation']['total_tokens'] == 240
    assert result['cases'][0]['error_type'] == 'HTTPException'
    assert 'fake' not in json.dumps(result)


def test_unknown_usage_is_not_zero_and_reasoning_not_double_counted():
    async def check():
        meter = Meter()
        for usage in ({'total_tokens': 120, 'completion_tokens_details': {'reasoning_tokens': 10}}, {}):
            response = httpx.Response(200, request=httpx.Request('POST', 'https://test/chat/completions'), json={'usage': usage})
            await meter.response(response)
        assert meter.calls[0]['total_tokens'] == 120
        assert meter.totals()['generation'] == {'requests': 2, 'total_tokens': None}
    asyncio.run(check())


def test_connection_failure_still_counts_as_attempt_with_unknown_usage():
    async def check():
        meter = Meter()
        await meter.request(httpx.Request('POST', 'https://test/chat/completions'))
        assert meter.totals()['generation'] == {'requests': 1, 'total_tokens': None}
    asyncio.run(check())
