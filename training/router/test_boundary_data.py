import json
from .boundary_data import DEST, additions
from .dataset import ROOT, read_examples, digest
from .targeted_data import DEST as PREVIOUS


def test_original_labels_and_holdouts_unchanged():
    old=read_examples(PREVIOUS/'train.jsonl')
    new=read_examples(DEST/'train.jsonl')
    assert len(new)==168
    assert [e.model_dump() for e in new[:132]]==[e.model_dump() for e in old]
    assert [e.model_dump() for e in new[132:]]==[e.model_dump() for e in additions()]
    manifest=json.loads((DEST/'manifest.json').read_text())
    assert manifest['holdout_hashes']=={
        'validation':'492e95fa5a14246268681aafdd4bddd3f1bc670d341c08f0b1631389e3748d8c',
        'test_candidate':'292078ca8a08f6cc163f4ab0da351dad13ddfc72f8c4f3fb170fc3cb6690c02d'}
    assert all(digest(ROOT/f'{k}.jsonl')==v for k,v in manifest['holdout_hashes'].items())


def test_missing_user_context_vs_supplied_context_changes_route():
    rows=additions()
    for offset,expected in ((0,'answer'),(2,'compare'),(4,'search'),(6,'download_pdf')):
        missing,present=rows[offset:offset+2]
        assert missing.state.goal==present.state.goal
        assert missing.expected.route=='clarify'
        assert present.expected.route==expected
        assert not missing.state.results and present.state.results


def test_optional_details_are_not_required_clarifications():
    for family,route in [('default_search','search'),('default_dimension','compare'),('default_answer','answer')]:
        rows=[e for e in additions() if e.scenario_group=='boundary_'+family]
        assert len(rows)==2
        assert all(e.expected.route==route for e in rows)
