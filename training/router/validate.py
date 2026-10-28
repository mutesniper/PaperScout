"""校验规格结构与操作可用性，不对语义标签评分。"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .contract import Example, ROUTES, allowed_routes, model_messages


def validate(path: Path) -> dict:
    raw = path.read_bytes()
    examples = []
    ids, states = set(), set()
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip():
            raise ValueError(f'Line {number}: blank row')
        try:
            example = Example.model_validate_json(line)
            fingerprint = json.dumps(example.state.model_dump(), sort_keys=True, ensure_ascii=False)
            if example.id in ids or fingerprint in states:
                raise ValueError('Duplicate ID or input state')
            ids.add(example.id)
            states.add(fingerprint)
            examples.append(example)
        except ValueError as error:
            raise ValueError(f'Line {number}: {error}') from error
    counts = Counter(e.expected.route for e in examples)
    if len(examples) != 60 or any(counts[r] != 10 for r in ROUTES):
        raise ValueError(f'Expected 60 specification examples, 10 per route; got {dict(counts)}')
    lengths = [sum(len(m['content']) for m in model_messages(e.state)) for e in examples]
    return {'kind': 'specification_validation_not_model_evaluation',
        'sha256': hashlib.sha256(raw).hexdigest(), 'examples':len(examples),
        'class_counts':dict(counts), 'scenario_groups':len({e.scenario_group for e in examples}),
        'multiple_available_routes':sum(len(allowed_routes(e.state)) > 1 for e in examples),
        'prompt_characters_min':min(lengths), 'prompt_characters_max':max(lengths),
        'independent_review':'pending', 'model_calls':0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path(__file__).with_name('specification.jsonl'))
    args = parser.parse_args()
    print(json.dumps(validate(args.data), ensure_ascii=False, indent=2))
