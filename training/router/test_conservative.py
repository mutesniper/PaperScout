"""验证续训数据不覆盖旧集，以及退化拦截规则不会被总分改善绕过。"""
import copy
import json

import pytest

from .conservative_data import additions, DEST
from .conservative_eval import adoption_gate
from .conservative_run import lr_at, selection_key
from .dataset import ROOT, canonical_input, read_examples
from .evaluate import score
from .layered_eval import aggregate, run_task
from .progression_data import DEST as PREVIOUS
from .task_suite import World, suite


def test_data_preserves_previous_and_separates_inputs():
    added=additions()
    assert len(added)==72
    assert len({e.scenario_group for e in added})==18
    old=(PREVIOUS/'train.jsonl').read_bytes()
    merged=(DEST/'train.jsonl').read_bytes()
    assert merged.startswith(old)
    train=read_examples(DEST/'train.jsonl')
    assert len(train)==300
    fingerprints={canonical_input(e) for e in train}
    assert len(fingerprints)==300
    for path in (ROOT/'validation.jsonl',ROOT/'test_candidate.jsonl',PREVIOUS/'probe.jsonl'):
        assert not fingerprints & {canonical_input(e) for e in read_examples(path)}
    exported=[json.loads(line) for line in (DEST/'train.messages.jsonl').read_text(encoding='utf-8').splitlines()]
    for row,e in zip(exported,train,strict=True):
        assert row['messages'][-1]['content']==e.expected.model_dump_json()
        assert e.id not in row['messages'][1]['content']


def perfect_report():
    examples=read_examples(ROOT/'validation.jsonl')
    return score(examples,[e.expected.model_dump_json() for e in examples])


def perfect_tasks():
    rows=[]
    for task in suite():
        shadow=World(task)
        def decide(state):
            choice=shadow.expected()
            shadow.execute(choice)
            return {'raw':json.dumps({'route':choice})}
        rows.append(run_task(task,decide))
    return aggregate(rows)


def test_gate_requires_gain_and_blocks_regression_even_with_higher_total():
    report=perfect_report()
    old={name:copy.deepcopy(report) for name in ('validation','probe','test_candidate')}
    new=copy.deepcopy(old)
    tasks=perfect_tasks()
    assert not adoption_gate(old,new,tasks,tasks)['eligible']
    old['validation']['rows'][0]['acceptable']=False
    assert adoption_gate(old,new,tasks,tasks)['eligible']
    new['test_candidate']['rows'][1]['acceptable']=False
    decision=adoption_gate(old,new,tasks,tasks)
    assert not decision['eligible']
    assert decision['paired_examples']['test_candidate']['regressed']


def test_gate_blocks_safety_and_task_regression():
    report=perfect_report()
    old={name:copy.deepcopy(report) for name in ('validation','probe','test_candidate')}
    new=copy.deepcopy(old)
    old['validation']['rows'][0]['acceptable']=False
    tasks=perfect_tasks()
    changed=copy.deepcopy(tasks)
    changed['trajectories'][0]['constraint_violation']=True
    decision=adoption_gate(old,new,tasks,changed)
    assert 'tasks: new_constraint_violation' in decision['reasons']
    new['probe']['allowed_rate']=0.99
    assert 'probe: allowed_rate_decreased' in adoption_gate(old,new,tasks,tasks)['reasons']
    changed=copy.deepcopy(tasks)
    changed['trajectories'][0]['task_success']=False
    assert 'tasks: previously_successful_regressed' in adoption_gate(old,new,tasks,changed)['reasons']
    del new['test_candidate']
    with pytest.raises(ValueError):adoption_gate(old,new,tasks,tasks)


def test_selection_and_learning_rate_are_fixed():
    reference=perfect_report()
    first={**copy.deepcopy(reference),'epoch':1}
    second={**copy.deepcopy(reference),'epoch':2}
    assert selection_key(first,reference)>selection_key(second,reference)
    first['rows'][0]['acceptable']=False
    assert selection_key(second,reference)>selection_key(first,reference)
    assert lr_at(1)<lr_at(8)
    assert lr_at(8)==pytest.approx(8e-6)
    assert lr_at(150)==pytest.approx(2e-6)
