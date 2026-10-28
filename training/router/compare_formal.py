"""Summarize completed validation runs without model calls or test access."""
import argparse
import json
from pathlib import Path


def gates(base, tuned):
    return {'schema':tuned['schema_rate']>=.99,
            'accuracy':tuned['acceptable_accuracy']>=.90,
            'macro_f1':tuned['macro_f1']>=.90,
            'every_class_recall':all(v['recall']>=.80 for v in tuned['per_class'].values()),
            'no_class_recall_regression':all(v['recall']>=base['per_class'][k]['recall'] for k,v in tuned['per_class'].items()),
            'no_illegal_routes_on_validation':tuned['allowed_rate']==1,
            'premature_finish':tuned['premature_finish_rate'] is not None and tuned['premature_finish_rate']<=.02,
            'gain_5_percentage_points':tuned['acceptable_accuracy']-base['acceptable_accuracy']>=.05}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    root=args.directory
    def read(name):
        return json.loads((root/name).read_text(encoding='utf-8'))
    result=read('result.json')
    base=read('base-validation.json')
    tuned=read('selected-reloaded-validation.json')
    check=read('reload_check.json')
    if result['status']!='complete' or not all(check.values()):
        raise ValueError('Training and independent reload must complete')
    for key in ('count','dataset_sha256','prompt_sha256','decoding'):
        if base[key]!=tuned[key]:
            raise ValueError(f'Incomparable {key}')
    before={r['id']:r for r in base['rows']}
    if set(before)!={r['id'] for r in tuned['rows']}:
        raise ValueError('Different evaluation examples')
    corrected=[r['id'] for r in tuned['rows'] if r['acceptable'] and not before[r['id']]['acceptable']]
    regressed=[r['id'] for r in tuned['rows'] if not r['acceptable'] and before[r['id']]['acceptable']]
    checks=gates(base,tuned)
    report={'selected_epoch':result['selected_epoch'],'validation_count':base['count'],
        'base_accuracy':base['acceptable_accuracy'],'finetuned_accuracy':tuned['acceptable_accuracy'],
        'gain_percentage_points':100*(tuned['acceptable_accuracy']-base['acceptable_accuracy']),
        'corrected_ids':corrected,'regressed_ids':regressed,'gates':checks,
        'all_numeric_validation_gates_pass':all(checks.values()),
        'not_deployment_approval':True,'independent_review':'pending',
        'test_evaluated':False,'agent_evaluated':False,
        'limitation':'Validation used for checkpoint selection; correlated synthetic cases, not independent test evidence.'}
    (root/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
