import json
from collections import Counter

import pytest

from training.router.contract import model_messages
from training.router.dataset import ROOT, digest, read_examples
from training.router.targeted_data import DEST, additions, smoke_examples
from training.router.qlora_smoke import encode_supervised


class CharacterTokenizer:
    eos_token_id=1000000

    def apply_chat_template(self,messages,tokenize,add_generation_prompt):
        prefix='SYS:'+messages[0]['content']+'USER:'+messages[1]['content']+'ASSISTANT:'
        if add_generation_prompt:
            return [ord(c) for c in prefix]
        return [ord(c) for c in prefix+messages[-1]['content']]+[self.eos_token_id,10]

    def decode(self,tokens,skip_special_tokens=False):
        return ''.join(chr(t) for t in tokens)


def test_only_json_and_eos_are_supervised():
    e=additions()[0]
    encoded=encode_supervised(CharacterTokenizer(),e,10000)
    n=encoded['prompt_length']
    assert set(encoded['labels'][:n])=={-100}
    assert encoded['labels'][n:]==[ord(c) for c in e.expected.model_dump_json()]+[1000000]
    assert encoded['input_ids'][-1]==1000000
    with pytest.raises(ValueError,match='no truncation'):
        encode_supervised(CharacterTokenizer(),e,10)


def test_unstable_template_cannot_silently_train_prompt_tokens():
    class BadTokenizer(CharacterTokenizer):
        def apply_chat_template(self,messages,tokenize,add_generation_prompt):
            values=super().apply_chat_template(messages,tokenize,add_generation_prompt)
            if not add_generation_prompt:
                values[0]=999
            return values
    with pytest.raises(ValueError,match='prefix'):
        encode_supervised(BadTokenizer(),additions()[0],10000)


def test_targeted_data_does_not_change_v2_splits():
    manifest=json.loads((DEST/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['holdout_hashes']=={
        'validation':'492e95fa5a14246268681aafdd4bddd3f1bc670d341c08f0b1631389e3748d8c',
        'test_candidate':'292078ca8a08f6cc163f4ab0da351dad13ddfc72f8c4f3fb170fc3cb6690c02d'}
    assert all(digest(ROOT/f'{s}.jsonl')==h for s,h in manifest['holdout_hashes'].items())
    assert len(read_examples(ROOT/'train.jsonl'))==84
    rows=read_examples(DEST/'train.jsonl')
    assert len(rows)==132
    assert [e.model_dump() for e in read_examples(DEST/'additions.jsonl')]==[e.model_dump() for e in additions()]


def test_smoke_has_no_heldout_items_and_covers_all_routes():
    rows=read_examples(DEST/'train.jsonl')
    chosen=smoke_examples(rows)
    assert len(chosen)==len({e.id for e in chosen})==48
    assert set(Counter(e.expected.route for e in chosen).values())=={8}
    assert all(e.split=='train' for e in chosen)
    assert [e.id for e in chosen]==[e.id for e in smoke_examples(rows)]


def test_same_goal_requires_different_actions_when_state_changes():
    selected=[e for e in additions() if e.scenario_group=='cf_compare']
    assert len({e.state.goal for e in selected})==1
    assert {e.expected.route for e in selected}=={'search','download_pdf','compare','finish'}
    for e in selected:
        payload=json.loads(model_messages(e.state)[1]['content'])
        assert not {'expected','rationale','scenario_group'} & payload.keys()
