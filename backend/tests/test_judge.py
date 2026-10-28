import asyncio
import json

import httpx
import pytest

from evaluation.judge import PROMPT_SHA256, Verdict
from evaluation.run import evaluate, markdown
from test_evaluation import dataset


@pytest.fixture
def configured(monkeypatch):
    for prefix, name in [('LLM', 'answer-model'), ('JUDGE', 'judge-model')]:
        monkeypatch.setenv(prefix + '_BASE_URL', 'https://test/v1')
        monkeypatch.setenv(prefix + '_API_KEY', 'secret-test-key')
        monkeypatch.setenv(prefix + '_MODEL', name)
    data = dataset()
    data.cases = [data.cases[0], data.cases[3]]
    return data


def reply(content, *, finish='stop', usage=True):
    body = {'choices': [{'message': {'content': json.dumps(content)}, 'finish_reason': finish}]}
    if usage:
        body['usage'] = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}
    return httpx.Response(200, json=body)


def answer_reply(body):
    material = json.loads(body['messages'][1]['content'])
    # 回答模型只接收问题和检索片段，不能看到参考答案。
    assert set(material) == {'question', 'excerpts'}
    if '方法' in material['question']:
        return reply({'supported': True, 'claims': [{'text': '测试结论',
            'evidence_ids': [material['excerpts'][0]['id']]}], 'reason': ''})
    return reply({'supported': False, 'claims': [], 'reason': '未报告费用。'})


def verdict(*, refused=False):
    return {'correctness': {'score': 1, 'reason': '仅覆盖部分事实'},
        'completeness': {'score': 1, 'reason': '缺少实验条件'},
        'grounding': None if refused else {'score': 0, 'reason': '结论不受引用支持'}}


def test_judge_is_opt_in_even_when_configured(configured):
    def handler(request):
        body = json.loads(request.content)
        assert body['model'] == 'answer-model'
        return answer_reply(body)
    report = asyncio.run(evaluate(configured, live=True, transport=httpx.MockTransport(handler)))
    assert report['judge'] == {'enabled': False}
    assert report['summary']['errors'] == 0


def test_judge_scores_are_separate_from_provenance_and_usage(configured):
    materials = []
    def handler(request):
        body = json.loads(request.content)
        if body['model'] == 'answer-model':
            return answer_reply(body)
        material = json.loads(body['messages'][1]['content'])
        materials.append(material)
        assert 'reference_answer' in material and 'gold_evidence' in material
        assert set(material['answer']) == {'supported', 'claims', 'reason'}
        return reply(verdict(refused=not material['answer']['supported']))
    report = asyncio.run(evaluate(configured, live=True, judge=True, transport=httpx.MockTransport(handler)))
    assert len(materials) == 2
    assert report['cases'][0]['citation_provenance_valid'] is True
    assert report['cases'][0]['judge']['grounding']['score'] == 0
    assert report['cases'][1]['judge']['grounding'] is None
    assert report['judge']['summary']['scores']['grounding'] == {'mean': 0, 'count': 1}
    assert report['judge']['summary']['scored'] == 2
    assert report['judge']['prompt_sha256'] == PROMPT_SHA256
    assert report['usage']['generation']['total_tokens'] == 240
    assert report['judge']['usage'] == {'requests': 2, 'total_tokens': 240}
    assert all(r['manual_review']['answer_correct'] is None for r in report['cases'])
    assert 'secret-test-key' not in json.dumps(report)
    assert '不是人工真值' in markdown(report)


@pytest.mark.parametrize('failure', ['http', 'invalid', 'truncated', 'wrong_null', 'timeout'])
def test_failed_judge_is_retained_without_retry_or_losing_answer(configured, failure):
    configured.cases = configured.cases[:1]
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body['model'])
        if body['model'] == 'answer-model':
            return answer_reply(body)
        if failure == 'http':
            return httpx.Response(502, text='secret-test-key')
        if failure == 'timeout':
            raise httpx.ReadTimeout('secret-test-key', request=request)
        if failure == 'invalid':
            return reply({'correctness': 'perfect'})
        if failure == 'wrong_null':
            return reply(verdict(refused=True))
        return reply(verdict(), finish='length')
    report = asyncio.run(evaluate(configured, live=True, judge=True, transport=httpx.MockTransport(handler)))
    assert calls == ['answer-model', 'judge-model']
    assert report['summary']['errors'] == 0
    assert report['cases'][0]['answer']['supported'] is True
    assert report['judge']['summary']['errors'] == 1
    assert report['judge']['summary']['scores']['correctness'] == {'mean': None, 'count': 0}
    assert 'secret-test-key' not in json.dumps(report)


def test_answer_failure_skips_judge(configured):
    def handler(request):
        assert json.loads(request.content)['model'] == 'answer-model'
        return reply({'supported': True, 'claims': [{'text': '虚构引用', 'evidence_ids': ['missing']}], 'reason': ''})
    report = asyncio.run(evaluate(configured, live=True, judge=True, transport=httpx.MockTransport(handler)))
    assert report['summary']['errors'] == 2
    assert report['judge']['summary']['skipped'] == 2
    assert report['judge']['usage']['requests'] == 0


def test_configuration_and_mode_checked_before_calls(configured, monkeypatch):
    def forbidden(request):
        pytest.fail('配置错误时不能发出请求')
    transport = httpx.MockTransport(forbidden)
    with pytest.raises(ValueError, match='--live'):
        asyncio.run(evaluate(configured, judge=True, transport=transport))
    monkeypatch.delenv('JUDGE_API_KEY')
    with pytest.raises(ValueError, match='JUDGE_API_KEY'):
        asyncio.run(evaluate(configured, live=True, judge=True, transport=transport))


@pytest.mark.parametrize('score', [True, '2', 3, -1, None])
def test_scores_cannot_be_coerced_or_out_of_range(score):
    data = verdict()
    data['correctness']['score'] = score
    with pytest.raises(ValueError):
        Verdict.model_validate(data)
