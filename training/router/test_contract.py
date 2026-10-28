import json
from pathlib import Path

import pytest

from training.router.build_specification import cases, candidate, pdf, state, failed_download
from training.router.contract import allowed_routes, model_messages, parse_decision
from training.router.validate import validate


@pytest.mark.parametrize('raw', [
    '```json\n{"route":"search"}\n```',
    '{"route":"search","reason":"because"}',
    '{"route":"search","route":"finish"}',
    '{"route":"read"}', '{"route":null}', '[{"route":"search"}]',
])
def test_raw_output_is_not_repaired(raw):
    with pytest.raises(ValueError):
        parse_decision(raw, state('找工具学习论文'))


def test_resource_permissions_do_not_encode_intent():
    resources = [pdf(), pdf('r2','p2','Toolformer')]
    answer = state('只解释第一篇', pdfs=resources)
    compare = state('比较这两篇', pdfs=resources)
    assert allowed_routes(answer) == allowed_routes(compare)
    assert {'answer','compare','search','clarify','finish'} <= set(allowed_routes(answer))


def test_candidate_and_reading_ids_are_distinct_namespaces():
    s = state('阅读新候选', candidates=[candidate('r1')], pdfs=[pdf()])
    assert 'download_pdf' in allowed_routes(s)


@pytest.mark.parametrize('kwargs', [
    {'pdfs':[pdf()]}, {'outcomes':[failed_download()]},
    {'remaining':{'download_pdf':0}},
])
def test_download_cannot_repeat_or_bypass_budget(kwargs):
    s = state('阅读论文', candidates=[candidate()], **kwargs)
    with pytest.raises(ValueError):
        parse_decision('{"route":"download_pdf"}', s)


@pytest.mark.parametrize('kwargs', [{'cancelled':True}, {'remaining':{'steps':0}}])
def test_terminal_state_never_dispatches_tools(kwargs):
    s = state('比较论文', pdfs=[pdf(),pdf('r2','p2','B')], **kwargs)
    assert allowed_routes(s) == ['finish']
    with pytest.raises(ValueError):
        parse_decision('{"route":"compare"}', s)


def test_compare_requires_two_readable_documents():
    with pytest.raises(ValueError):
        parse_decision('{"route":"compare"}', state('对比', pdfs=[pdf()]))


def test_annotation_does_not_leak_into_model_input():
    example = cases()[0]
    payload = json.loads(model_messages(example.state)[1]['content'])
    assert not {'expected','acceptable_routes','rationale','tags','scenario_group','review'} & payload.keys()
    assert payload['goal'] == example.state.goal
    assert payload['remaining']['search'] == 2


def test_artifact_reproducible_and_balanced():
    path = Path(__file__).with_name('specification.jsonl')
    stored = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert stored == [e.model_dump() for e in cases()]
    report = validate(path)
    assert report['examples'] == 60
    assert report['model_calls'] == 0


def test_duplicate_data_rejected(tmp_path):
    path = tmp_path / 'duplicate.jsonl'
    line = cases()[0].model_dump_json() + '\n'
    path.write_text(line * 2, encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate'):
        validate(path)
