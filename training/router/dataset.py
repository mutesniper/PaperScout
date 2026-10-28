"""Authored task-state datasets, without neutral-prefix or ID augmentation."""
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from .build_specification import cases
from .contract import Example, ROUTES, SYSTEM_PROMPT, model_messages
from .holdout_scenarios import seeds
from .diverse_scenarios import families

ROOT = Path(__file__).with_name('data')
TARGETS = {'train':84, 'validation':36, 'test_candidate':120}
PREFIXES = ('', '请帮我处理这个研究任务：', '当前要求是：', '我的研究需求如下：',
            '请按这个目标推进：', '我需要你做这件事：', '接下来的任务：',
            '请协助处理：', '本次研究目标：', '请依据当前资料处理这个需求：')


class DatasetExample(Example):
    parent_id: str
    augmentation: Literal['none'] = 'none'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parents(split):
    if split == 'train':
        # Previously exposed specs and ALL v1 holdouts are now development data.
        result = [e.model_copy(update={'split':'train'}) for e in cases()]
        aliases = {
            'v_unavailable_named':'one_missing', 'v_no_substitution':'selected_target',
            't_selected_fourth':'selected_target', 'v_excluded_compare':'same_resources',
            't_refute_user_claim':'same_resources', 'v_search_already_done':'tiny_answer',
            'v_answer_budget_zero':'budget_compare', 'v_ambiguous_subset':'ambiguous_comparison',
            't_recover_different_id':'fallback_candidate', 't_same_title_unknown_choice':'ambiguous_revision',
            't_ordered_subtasks':'ordered_subtasks', 't_ordered_after_answer':'ordered_subtasks',
            't_only_two_selected':'specific_pair', 't_link_count_met':'links_ready',
        }
        for old_split in ('validation','test_candidate'):
            for route,group,s,why in seeds(old_split):
                result.append(Example(id=group,scenario_group=aliases.get(group,group),split='train',
                    state=s,expected={'route':route},acceptable_routes=[route],rationale=why,
                    tags=['retired_v1_holdout','development_only']))
        return result
    return [Example(id=f'{family}-{i+1:02}', scenario_group=family,split=split,state=s,
        expected={'route':route},acceptable_routes=[route],rationale=why,
        tags=['authored_task_branch',family,'independent_review_pending'])
        for assigned,family,rows in families() if assigned==split
        for i,(route,s,why) in enumerate(rows)]


def generate(split):
    result = [DatasetExample.model_validate({**p.model_dump(), 'parent_id':p.id,
        'id':f'{split}-{p.id}', 'augmentation':'none'}) for p in parents(split)]
    random.Random(20261028).shuffle(result)
    return result


def read_examples(path):
    return [DatasetExample.model_validate_json(line)
            for line in Path(path).read_text(encoding='utf-8').splitlines()]


def canonical_input(example):
    """Catch our neutral-prefix/ID variants even if someone changes group names."""
    state = example.state.model_dump()
    for prefix in PREFIXES[1:]:
        if state['goal'].startswith(prefix):
            state['goal'] = state['goal'][len(prefix):]
            break
    for item in [*state['candidates'], *state['available_pdfs'], *state['recent_outcomes']]:
        for key in ('paper_id','reading_id','target_id'):
            if item.get(key):
                item[key] = re.sub(r'-v\d+$','',item[key])
    return json.dumps(state,sort_keys=True,ensure_ascii=False)


def coverage(examples):
    return {
        'no_pdf':sum(not e.state.available_pdfs for e in examples),
        'one_pdf':sum(len(e.state.available_pdfs)==1 for e in examples),
        'multiple_pdfs':sum(len(e.state.available_pdfs)>=2 for e in examples),
        'no_candidates':sum(not e.state.candidates for e in examples),
        'closed_candidate':sum(any(not c.public_pdf for c in e.state.candidates) for e in examples),
        'prior_results':sum(bool(e.state.results) for e in examples),
        'download_failure':sum(any(o.status=='failed' for o in e.state.recent_outcomes) for e in examples),
        'zero_tool_budget':sum(any(v==0 for k,v in e.state.remaining.model_dump().items() if k!='steps') for e in examples),
        'zero_steps':sum(e.state.remaining.steps==0 for e in examples),
        'cancelled':sum(e.state.cancelled for e in examples),
    }


def audit(datasets):
    groups, parents_seen, fingerprints, ids = {}, {}, {}, set()
    normalized, goals = {}, {}
    summary = {}
    for split, examples in datasets.items():
        counts = Counter(e.expected.route for e in examples)
        if len(examples) != TARGETS[split]:
            raise ValueError(f'Wrong size: {split}')
        expected_counts = {r:TARGETS[split]//6 + (i<TARGETS[split]%6) for i,r in enumerate(ROUTES)}
        if dict(counts) != expected_counts:
            raise ValueError(f'Wrong class counts: {split}')
        for e in examples:
            if e.split != split or e.id in ids:
                raise ValueError('Wrong split or duplicate ID')
            ids.add(e.id)
            if e.augmentation != 'none' or e.parent_id in parents_seen:
                raise ValueError('Duplicated parent/augmentation is not allowed in v2')
            for mapping, key in ((groups,e.scenario_group), (parents_seen,e.parent_id)):
                if key in mapping and mapping[key] != split:
                    raise ValueError('Scenario family/parent crosses splits')
                mapping[key] = split
            fingerprint = json.dumps(e.state.model_dump(),sort_keys=True,ensure_ascii=False)
            if fingerprint in fingerprints:
                raise ValueError('Duplicate input state')
            fingerprints[fingerprint] = split
            canonical = canonical_input(e)
            if canonical in normalized and normalized[canonical] != split:
                raise ValueError('Augmented input crosses splits despite different group names')
            normalized[canonical] = split
            goal_key = re.sub(r'\s+','',e.state.goal.casefold())
            if goal_key in goals and goals[goal_key] != split:
                raise ValueError('Same task goal crosses splits')
            goals[goal_key] = split
        summary[split] = {'rows':len(examples), 'class_counts':dict(counts),
            'parent_scenarios':len({e.parent_id for e in examples}),
            'scenario_groups':len({e.scenario_group for e in examples}),
            'coverage':coverage(examples)}
        if split in ('validation','test_candidate'):
            for key in ('no_pdf','one_pdf','multiple_pdfs','prior_results','download_failure','closed_candidate',
                        'zero_tool_budget','zero_steps','cancelled'):
                if summary[split]['coverage'][key] == 0:
                    raise ValueError(f'Missing coverage: {split}/{key}')
    return summary


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')


def build(root=ROOT):
    datasets = {split:generate(split) for split in TARGETS}
    summary = audit(datasets)
    root.mkdir(parents=True,exist_ok=True)
    for split, rows in datasets.items():
        write_jsonl(root/f'{split}.jsonl', [e.model_dump() for e in rows])
    # Training export excludes annotation, scenario identifiers, labels in user input.
    write_jsonl(root/'train.messages.jsonl', [
        {'messages':model_messages(e.state) + [{'role':'assistant','content':e.expected.model_dump_json()}]}
        for e in datasets['train']])
    manifest = {'version':'router-corpus-v2', 'seed':20261028, 'status':'authored_corpus_not_model_validated',
        'independent_review':'pending', 'semantic_split_review':'author_reviewed_compositional_holdout',
        'test_status':'candidate_not_independently_sealed_not_scored',
        'augmentation':'none; one authored state per row; branches within a task family are correlated',
        'v1_holdout_disposition':'All 24 old validation/test seeds moved to train; never evaluate as held-out again',
        'datasets':summary,
        'system_prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        'files':{p.name:digest(p) for p in sorted(root.glob('*.jsonl'))}}
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
