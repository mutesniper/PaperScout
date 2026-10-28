"""候选与当前版同条件评测；任何已正确样例退化均拒绝替换。"""
import argparse
import hashlib
import json
import os
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from .conservative_run import DEFAULT_ADAPTER, assert_unchanged, regressions
from .contract import SYSTEM_PROMPT, model_messages
from .dataset import ROOT, digest, read_examples
from .formal_train import evaluate, write
from .layered_eval import aggregate, run_task
from .local_baseline import FILES, check_model, generate_greedy
from .progression_data import DEST as PROGRESSION
from .qlora_smoke import load_base
from .task_suite import suite


def adoption_gate(reference, candidate, old_tasks, new_tasks):
    reasons=[]
    paired={}
    gain=False
    if set(reference)!=set(candidate) or set(reference)!={'validation','probe','test_candidate'}:
        raise ValueError('必须完成三个固定数据集的对照')
    for name,old in reference.items():
        new=candidate[name]
        lost=regressions(old,new)
        gained=[a['id'] for a,b in zip(old['rows'],new['rows'],strict=True)
                if not a['acceptable'] and b['acceptable']]
        paired[name]={'fixed':gained,'regressed':lost}
        gain=gain or bool(gained)
        if lost:reasons.append(f'{name}: previously_correct_regressed')
        for metric in ('schema_rate','allowed_rate','acceptable_accuracy','macro_f1','group_macro_accuracy'):
            if new[metric]+1e-12<old[metric]:reasons.append(f'{name}: {metric}_decreased')
        if (new['premature_finish_rate'] or 0)>(old['premature_finish_rate'] or 0)+1e-12:
            reasons.append(f'{name}: premature_finish_increased')
        for label,row in old['per_class'].items():
            if new['per_class'][label]['recall']+1e-12<row['recall']:
                reasons.append(f'{name}: {label}_recall_decreased')
    old_rows=old_tasks['trajectories']
    new_rows=new_tasks['trajectories']
    if [t['task_id'] for t in old_rows]!=[t['task_id'] for t in new_rows]:
        raise ValueError('模拟任务必须完全一致且同序')
    lost_tasks=[a['task_id'] for a,b in zip(old_rows,new_rows,strict=True) if a['task_success'] and not b['task_success']]
    fixed_tasks=[a['task_id'] for a,b in zip(old_rows,new_rows,strict=True) if not a['task_success'] and b['task_success']]
    new_violations=[a['task_id'] for a,b in zip(old_rows,new_rows,strict=True) if not a['constraint_violation'] and b['constraint_violation']]
    lost_initial=[a['task_id'] for a,b in zip(old_rows,new_rows,strict=True)
                  if a['trace'][0]['correct_route'] and not b['trace'][0]['correct_route']]
    if lost_tasks:reasons.append('tasks: previously_successful_regressed')
    if new_violations:reasons.append('tasks: new_constraint_violation')
    if lost_initial:reasons.append('tasks: initial_route_regressed')
    for metric in ('premature_finishes','unnecessary_clarifications'):
        if new_tasks['layer1'][metric]>old_tasks['layer1'][metric]:reasons.append(f'tasks: {metric}_increased')
    for metric in ('research_completion_rate','controlled_stop_rate'):
        if new_tasks['layer3'][metric]<old_tasks['layer3'][metric]:reasons.append(f'tasks: {metric}_decreased')
    gain=gain or bool(fixed_tasks)
    if not gain:reasons.append('no_measured_quality_gain')
    return {'eligible':not reasons,'decision':'eligible_for_replacement' if not reasons else 'retain_incumbent',
        'reasons':reasons,'paired_examples':paired,
        'paired_tasks':{'fixed':fixed_tasks,'regressed':lost_tasks,'new_violations':new_violations,'initial_regressions':lost_initial},
        'applied':False,'note':'质量与安全门槛；单次耗时受环境影响，仅记录，不据此声称加速。'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--training-run',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer
    model_path=check_model(args.model_path)
    reg=json.loads((args.training_run/'preregistered.json').read_text())
    assert_unchanged(reg)
    if reg['base_files']!={f:digest(model_path/f) for f in FILES}:raise ValueError('基座发生变化')
    if reg['source_files']!={p:digest(p) for p in reg['source_files']}:raise ValueError('登记后代码变化')
    result=json.loads((args.training_run/'result.json').read_text())
    if result['status']!='complete':raise ValueError('训练未完成')
    if not all(json.loads((args.training_run/'reload_check.json').read_text()).values()):raise ValueError('重载检查失败')
    candidate=args.training_run/f"epoch-{result['selected_epoch']}"/'adapter'
    out=args.output_dir
    out.mkdir(parents=True,exist_ok=False)
    # 此标记防止根据首次测试成绩重跑并挑选结果。
    (args.training_run/'acceptance_started').open('x').close()
    datasets={'validation':ROOT/'validation.jsonl','probe':PROGRESSION/'probe.jsonl',
              'test_candidate':ROOT/'test_candidate.jsonl'}
    tasks=suite()
    write(out/'fixtures.json',[{**asdict(t),'initial':t.initial.model_dump()} for t in tasks])
    write(out/'metadata.json',{'status':'started','dataset_hashes':{k:digest(p) for k,p in datasets.items()},
        'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        'candidate_weights_sha256':digest(candidate/'adapter_model.safetensors'),
        'incumbent_weights_sha256':digest(DEFAULT_ADAPTER/'adapter_model.safetensors'),
        'fixture_sha256':digest(out/'fixtures.json'),'test_used_for_selection':False,'cloud_calls':0,
        'limitations':['合成自审数据，不是独立人工基准。','工具返回与参数绑定为模拟，不测真实答案质量。',
                       '旧验证、诊断和任务已被观察；测试候选此前未评分，但决策概念与训练共享。']})
    try:
        torch.manual_seed(20261028)
        tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True,trust_remote_code=False)
        model=PeftModel.from_pretrained(load_base(model_path,False),DEFAULT_ADAPTER,local_files_only=True)
        model.load_adapter(candidate,adapter_name='candidate',local_files_only=True)
        reports={}
        layered={}
        def decide(state):
            prompt=tokenizer.apply_chat_template(model_messages(state),tokenize=False,add_generation_prompt=True)
            tokens=tokenizer(prompt,return_tensors='pt',add_special_tokens=False,truncation=False)
            n=tokens['input_ids'].shape[-1]
            if n>2048:raise ValueError('任务状态超长，不截断')
            tokens={k:v.to('cuda') for k,v in tokens.items()}
            torch.cuda.synchronize()
            started=time.perf_counter()
            with torch.inference_mode():ids=generate_greedy(model,tokens,model.generation_config)[0,n:].tolist()
            torch.cuda.synchronize()
            return {'raw':tokenizer.decode(ids,skip_special_tokens=True),'input_tokens':n,'output_tokens':len(ids),
                    'seconds':time.perf_counter()-started,'hit_output_limit':len(ids)>=32}
        for label in ('base','incumbent','candidate'):
            model.set_adapter('candidate' if label=='candidate' else 'default')
            reports[label]={}
            with model.disable_adapter() if label=='base' else nullcontext():
                for name,path in datasets.items():
                    reports[label][name]=evaluate(model,tokenizer,read_examples(path),out/f'{label}-{name}.json',path)
                    print(json.dumps({'model':label,'dataset':name,'accuracy':reports[label][name]['acceptable_accuracy']}),flush=True)
                decide(tasks[0].initial)
                trajectories=[]
                for task in tasks:
                    trajectory=run_task(task,decide)
                    trajectories.append(trajectory)
                    with (out/f'{label}-trajectories.jsonl').open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(trajectory,ensure_ascii=False)+'\n')
                    print(json.dumps({'model':label,'task':task.id,'success':trajectory['task_success']}),flush=True)
                layered[label]=aggregate(trajectories)
                write(out/f'{label}-layered.json',layered[label])
        assert_unchanged(reg)
        gate=adoption_gate(reports['incumbent'],reports['candidate'],layered['incumbent'],layered['candidate'])
        write(out/'decision.json',gate)
        write(out/'summary.json',{'status':'complete','selected_epoch':result['selected_epoch'],
            'reports':{label:{name:{k:v for k,v in r.items() if k not in ('rows','measurements')}
                              for name,r in values.items()} for label,values in reports.items()},
            'tasks':{label:{k:v for k,v in r.items() if k!='trajectories'} for label,r in layered.items()},
            'decision':gate})
        meta=json.loads((out/'metadata.json').read_text())
        meta['status']='complete'
        write(out/'metadata.json',meta)
        print(json.dumps(gate,ensure_ascii=False),flush=True)
    except BaseException as exc:
        write(out/'incomplete.json',{'error_type':type(exc).__name__})
        raise


if __name__=='__main__':
    main()
