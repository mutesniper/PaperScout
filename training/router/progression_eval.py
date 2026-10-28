"""选模后在冻结的进度诊断集上执行一次对照，不进行训练。"""
import argparse
import hashlib
import json
import os
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from .dataset import digest, read_examples
from .contract import SYSTEM_PROMPT, model_messages
from .formal_train import evaluate, write
from .local_baseline import FILES, check_model, file_sha256, generate_greedy
from .layered_eval import run_task, aggregate
from .task_suite import suite
from .progression_data import DEST
from .qlora_smoke import load_base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--previous-run', type=Path, default=Path('outputs/router/formal-20261028-02'))
    parser.add_argument('--training-run', type=Path, default=Path('outputs/router/formal-20261028-03'))
    parser.add_argument('--output-dir', type=Path, default=Path('outputs/router/progression-eval-20261028-01'))
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer
    model_path = check_model(args.model_path)
    hashes = {f:file_sha256(model_path/f) for f in FILES}
    probe_path = DEST/'probe.jsonl'
    current = json.loads((args.training_run/'preregistered.json').read_text())
    prompt_hash = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
    if current['probe_sha256'] != digest(probe_path):
        raise ValueError('Probe changed since registration')
    checkpoints = {}
    for label, folder in [('previous',args.previous_run), ('current',args.training_run)]:
        run = json.loads((folder/'result.json').read_text())
        registration = json.loads((folder/'preregistered.json').read_text())
        if run['status'] != 'complete' or registration['base_files'] != hashes:
            raise ValueError('Incomplete run or wrong base')
        if registration['prompt_sha256'] != prompt_hash:
            raise ValueError('Prompt changed since training')
        reload_check = json.loads((folder/'reload_check.json').read_text())
        if not all(reload_check.values()):
            raise ValueError('Independent reload check failed')
        checkpoints[label] = folder/f"epoch-{run['selected_epoch']}"/'adapter'
    examples = read_examples(probe_path)
    tasks = suite()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    write(output/'fixtures.json', [{**asdict(t),'initial':t.initial.model_dump()} for t in tasks])
    write(output/'metadata.json', {'status':'started', 'probe_sha256':digest(probe_path),
        'base_files':hashes, 'selection':'legacy validation; probe never used for selection',
        'adapter_files':{k:{f.name:file_sha256(f) for f in p.iterdir() if f.is_file()}
                         for k,p in checkpoints.items()},
        'runner_sha256':digest(Path(__file__)),
        'fixture_sha256':digest(output/'fixtures.json'),
        'simulator_sha256':digest(Path(__file__).with_name('task_suite.py')),
        'cloud_calls':0, 'independent_review':'pending', 'test_candidate_calls':0})
    try:
        torch.manual_seed(20261028)
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
        model = PeftModel.from_pretrained(load_base(model_path,False), checkpoints['current'], local_files_only=True)
        model.load_adapter(checkpoints['previous'], adapter_name='previous', local_files_only=True)
        reports = {}
        layered = {}
        def decide(state):
            prompt = tokenizer.apply_chat_template(model_messages(state),tokenize=False,add_generation_prompt=True)
            inputs = tokenizer(prompt,return_tensors='pt',add_special_tokens=False)
            n = inputs['input_ids'].shape[-1]
            if n > 2048:
                raise ValueError('No truncation; task state exceeds 2048 tokens')
            inputs = {k:v.to('cuda') for k,v in inputs.items()}
            torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                ids = generate_greedy(model,inputs,model.generation_config)[0,n:].tolist()
            torch.cuda.synchronize()
            return {'raw':tokenizer.decode(ids,skip_special_tokens=True),'token_ids':ids,
                'input_tokens':n,'output_tokens':len(ids),'seconds':time.perf_counter()-start,
                'hit_output_limit':len(ids)>=32}
        for label in ('base','previous','current'):
            model.set_adapter('previous' if label == 'previous' else 'default')
            with model.disable_adapter() if label == 'base' else nullcontext():
                reports[label] = evaluate(model,tokenizer,examples,output/f'{label}.json',probe_path)
            print(json.dumps({'mode':label, 'count':reports[label]['count'],
                'accuracy':reports[label]['acceptable_accuracy'],'macro_f1':reports[label]['macro_f1']}),flush=True)
            with model.disable_adapter() if label == 'base' else nullcontext():
                decide(tasks[0].initial)
                trajectories = []
                for task in tasks:
                    result = run_task(task,decide)
                    trajectories.append(result)
                    with (output/f'{label}-trajectories.jsonl').open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(result,ensure_ascii=False)+'\n')
                    print(json.dumps({'mode':label,'task':task.id,'success':result['task_success']}),flush=True)
                layered[label] = aggregate(trajectories)
                write(output/f'{label}-layered.json',layered[label])
        write(output/'comparison.json', {'status':'complete','probe_sha256':digest(probe_path),
            'reports':{k:{f:v for f,v in r.items() if f not in ('rows','measurements')} for k,r in reports.items()},
            'paired_rows':[{'id':e.id,**{k:r['rows'][i] for k,r in reports.items()}} for i,e in enumerate(examples)],
            'limitations':['Synthetic self-reviewed cases, not human gold',
                           'Shared decision concepts with training; no claim of independent task families',
                           'One-step routing only; real answer quality not evaluated']})
        write(output/'layered-comparison.json', {'status':'complete','simulation':True,
            'fixture_sha256':digest(output/'fixtures.json'),
            'reports':{k:{f:v for f,v in r.items() if f!='trajectories'} for k,r in layered.items()},
            'paired_tasks':[{'id':t.id,**{k:r['trajectories'][i]['task_success'] for k,r in layered.items()}}
                            for i,t in enumerate(tasks)],
            'limitations':['Previously observed synthetic task suite; regression diagnostic only',
                           'Shared ideal parameter resolver and simulated tool receipts',
                           'Real retrieval, argument generation and answer quality not evaluated']})
        meta = json.loads((output/'metadata.json').read_text())
        meta['status'] = 'complete'
        write(output/'metadata.json',meta)
    except BaseException as exc:
        write(output/'incomplete.json',{'error_type':type(exc).__name__})
        raise


if __name__ == '__main__':
    main()
