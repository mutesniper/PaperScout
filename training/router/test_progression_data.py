import json

from .boundary_data import DEST as PREVIOUS
from .dataset import ROOT, canonical_input, digest, read_examples
from .progression_data import DEST, additions, probe


def test_prior_labels_and_reserved_splits_unchanged():
    rows = read_examples(DEST/'train.jsonl')
    assert len(rows) == 228
    assert rows[:168] == read_examples(PREVIOUS/'train.jsonl')
    assert rows[168:] == additions()
    manifest = json.loads((DEST/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['holdout_hashes'] == {
        'validation':'492e95fa5a14246268681aafdd4bddd3f1bc670d341c08f0b1631389e3748d8c',
        'test_candidate':'292078ca8a08f6cc163f4ab0da351dad13ddfc72f8c4f3fb170fc3cb6690c02d'}
    assert all(digest(ROOT/f'{k}.jsonl') == v for k,v in manifest['holdout_hashes'].items())


def test_probe_cannot_enter_training():
    training = read_examples(DEST/'train.jsonl')
    audit = probe()
    assert len(audit) == 36
    assert set(e.scenario_group for e in audit).isdisjoint(e.scenario_group for e in training)
    assert set(canonical_input(e) for e in audit).isdisjoint(canonical_input(e) for e in training)
    assert all(e.split == 'validation' for e in audit)


def test_order_and_missing_resources_have_distinct_next_actions():
    groups = {}
    for e in additions():
        groups.setdefault(e.scenario_group, []).append(e)
    assert [e.expected.route for e in groups['progression_pair_missing']] == [
        'search','download_pdf','compare','finish']
    assert [e.expected.route for e in groups['progression_ordered_read']] == [
        'answer','compare','answer','finish']
    assert [e.expected.route for e in groups['progression_literal_stop']] == [
        'finish','download_pdf','answer','search']
