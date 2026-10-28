import json

import pytest

from training.router.contract import model_messages
from training.router.dataset import ROOT, TARGETS, audit, build, generate, read_examples
from training.router.evaluate import score


def test_committed_artifacts_match_generator():
    datasets = {split:read_examples(ROOT/f'{split}.jsonl') for split in TARGETS}
    summary = audit(datasets)
    assert summary['train']['parent_scenarios'] == 84
    assert summary['test_candidate']['parent_scenarios'] == 120
    for split, rows in datasets.items():
        assert [e.model_dump() for e in rows] == [e.model_dump() for e in generate(split)]


def test_rebuild_preserves_hashes(tmp_path):
    expected = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    assert build(tmp_path)['files'] == expected['files']


def test_cannot_move_same_family_to_other_split():
    datasets = {split:generate(split) for split in TARGETS}
    datasets['validation'][0].scenario_group = datasets['train'][0].scenario_group
    with pytest.raises(ValueError,match='parent|crosses splits'):
        audit(datasets)


def test_cannot_move_same_parent_to_other_split():
    datasets = {split:generate(split) for split in TARGETS}
    datasets['validation'][0].parent_id = datasets['train'][0].parent_id
    with pytest.raises(ValueError,match='parent|crosses splits'):
        audit(datasets)


def test_messages_contain_only_prompt_state_and_answer():
    rows = read_examples(ROOT/'train.jsonl')
    exported = [json.loads(line) for line in (ROOT/'train.messages.jsonl').read_text(encoding='utf-8').splitlines()]
    for e, row in zip(rows,exported,strict=True):
        assert row['messages'][:2] == model_messages(e.state)
        assert json.loads(row['messages'][2]['content']) == {'route':e.expected.route}
        assert row['messages'][2]['role'] == 'assistant'
        payload = json.loads(row['messages'][1]['content'])
        assert not {'expected','acceptable_routes','rationale','parent_id','scenario_group'} & payload.keys()


def test_renamed_groups_cannot_hide_augmentation_leakage():
    datasets = {split:generate(split) for split in TARGETS}
    seed = next(e for e in datasets['train'] if e.expected.route=='search')
    leaked = seed.model_copy(deep=True)
    leaked.state.goal = '当前要求是：' + leaked.state.goal
    leaked.split = 'validation'
    leaked.id = 'pretend-new-id'
    leaked.parent_id = 'pretend-new-parent'
    leaked.scenario_group = 'pretend-new-group'
    index = next(i for i,e in enumerate(datasets['validation']) if e.expected.route=='search')
    datasets['validation'][index] = leaked
    with pytest.raises(ValueError,match='Augmented input crosses'):
        audit(datasets)


def test_no_variants_and_complete_evaluation_coverage():
    for split in TARGETS:
        rows = generate(split)
        assert len({e.parent_id for e in rows}) == len(rows)
        assert all(e.augmentation=='none' for e in rows)
    summary = audit({split:generate(split) for split in TARGETS})
    assert set(summary['test_candidate']['class_counts'].values()) == {20}
    assert all(v>0 for v in summary['test_candidate']['coverage'].values())
    assert summary['validation']['coverage']['no_pdf'] > 0


def test_all_old_holdouts_retired_to_train():
    from training.router.holdout_scenarios import seeds
    train = {e.parent_id for e in generate('train')}
    for split in ('validation','test_candidate'):
        assert {row[1] for row in seeds(split)} <= train


def test_similarity_finds_renamed_goal_paraphrase():
    from training.router.similarity import neighbours
    a = generate('train')[0].model_copy(deep=True)
    b = generate('validation')[0].model_copy(deep=True)
    a.state.goal = '解释Paper A的训练方法'
    b.state.goal = '当前要求是：解释Paper B的训练方法'
    hits = neighbours({'train':[a],'validation':[b]})
    assert len(hits) == 1
    assert hits[0]['goal_similarity'] == 1


def test_neighbour_review_matches_full_input_hashes():
    from training.router.review_neighbours import make_review
    recorded = json.loads((ROOT.parent/'similarity_review.json').read_text(encoding='utf-8'))
    assert make_review({split:generate(split) for split in TARGETS}) == recorded


def test_audit_rejects_missing_boundary_coverage():
    datasets = {split:generate(split) for split in TARGETS}
    for e in datasets['test_candidate']:
        e.state.cancelled = False
    with pytest.raises(ValueError,match='Missing coverage.*cancelled'):
        audit(datasets)


def test_invalid_output_counts_as_failure_not_dropped():
    e = next(e for e in generate('validation') if e.expected.route=='search')
    report = score([e,e,e],['{"route":"search"}','```json\n{"route":"search"}\n```','{"route":"finish"}'])
    assert report['count'] == 3
    assert report['schema_rate'] == pytest.approx(2/3)
    assert report['acceptable_accuracy'] == pytest.approx(1/3)
    assert report['premature_finish_rate'] == pytest.approx(1/3)
    assert report['confusion_matrix']['search']['invalid'] == 1


def test_legal_json_can_still_violate_resource_constraints():
    e = next(e for e in generate('train') if not e.state.available_pdfs)
    report = score([e],['{"route":"answer"}'])
    assert report['schema_rate'] == 1
    assert report['allowed_rate'] == 0
    assert report['acceptable_accuracy'] == 0


def test_model_output_count_must_match_input_count():
    with pytest.raises(ValueError):
        score(generate('validation'), [])
