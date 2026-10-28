"""Offline evaluation with raw-output scoring, shared denominators and failures."""
import argparse
import json
from pathlib import Path

from .contract import ROUTES, allowed_routes, parse_decision, parse_output
from .dataset import ROOT, digest, read_examples
from .rule_baseline import VERSION, route


def score(examples, outputs):
    if len(examples) != len(outputs) or not examples:
        raise ValueError('Every example needs exactly one output')
    matrix = {r:{p:0 for p in (*ROUTES,'invalid')} for r in ROUTES}
    rows = []
    for e, raw in zip(examples,outputs,strict=True):
        predicted, error = 'invalid', None
        schema_valid = False
        try:
            decision = parse_output(raw)
            schema_valid = True
            predicted = decision.route
            parse_decision(raw,e.state)
        except (ValueError, TypeError) as exc:
            error = type(exc).__name__
        permitted = schema_valid and predicted in allowed_routes(e.state)
        hit = permitted and predicted in e.acceptable_routes
        matrix[e.expected.route][predicted] += 1
        rows.append({'id':e.id,'scenario_group':e.scenario_group,'expected':e.expected.route,
            'raw':raw,'predicted':predicted,'schema_valid':schema_valid,'permitted':permitted,
            'acceptable':hit,'exact':permitted and predicted==e.expected.route,'error':error})
    per_class = {}
    for label in ROUTES:
        support = sum(matrix[label].values())
        tp = sum(r['exact'] and r['expected']==label for r in rows)
        predicted_count = sum(matrix[r][label] for r in ROUTES)
        precision = tp/predicted_count if predicted_count else 0
        recall = tp/support if support else 0
        per_class[label] = {'support':support,'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0}
    continuing = [r for r,e in zip(rows,examples,strict=True) if 'finish' not in e.acceptable_routes]
    group_scores = {}
    for group in {e.scenario_group for e in examples}:
        subset = [r for r in rows if r['scenario_group']==group]
        group_scores[group] = sum(r['acceptable'] for r in subset)/len(subset)
    n = len(rows)
    return {'count':n,'schema_rate':sum(r['schema_valid'] for r in rows)/n,
        'allowed_rate':sum(r['permitted'] for r in rows)/n,
        'acceptable_accuracy':sum(r['acceptable'] for r in rows)/n,
        'exact_accuracy':sum(r['exact'] for r in rows)/n,
        'macro_f1':sum(v['f1'] for v in per_class.values())/len(ROUTES),
        'group_macro_accuracy':sum(group_scores.values())/len(group_scores),
        'premature_finish_rate':sum(r['predicted']=='finish' for r in continuing)/len(continuing) if continuing else None,
        'per_class':per_class,'confusion_matrix':matrix,'rows':rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--split',choices=['train','validation'],default='validation')
    parser.add_argument('--output',type=Path,default=None)
    args = parser.parse_args()
    args.output = args.output or Path(f'outputs/router/rules-v2-{args.split}.json')
    path = ROOT/f'{args.split}.jsonl'
    examples = read_examples(path)
    outputs = [json.dumps({'route':route(e.state)}) for e in examples]
    report = score(examples,outputs)
    report.update({'model':VERSION,'dataset_sha256':digest(path),'split':args.split,
        'rule_sha256':digest(Path(__file__).with_name('rule_baseline.py')),
        'model_calls':0,'independent_review':'pending','synthetic_augmented_data':False,
        'dataset_version':'router-corpus-v2','evaluation_unit':'authored state; task-family branches correlated'})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','confusion_matrix','per_class')},ensure_ascii=False,indent=2))
