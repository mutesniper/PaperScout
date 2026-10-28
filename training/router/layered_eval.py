"""Local models, simulated tools: layered controlled-task evaluation only."""
import argparse
import hashlib
import json
import os
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from .contract import SYSTEM_PROMPT, model_messages, parse_output, allowed_routes
from .dataset import digest
from .local_baseline import check_model, file_sha256, FILES, GREEDY_OPTIONS, generate_greedy, percentile
from .qlora_smoke import load_base, adapter_hashes
from .task_suite import World, suite


def save(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def run_task(task,decide):
    world=World(task)
    trace=[]
    reason='step_limit'
    # Include terminal decision; hard cap also bounds repeated clarification.
    for step in range(12):
        state=world.state.model_copy(deep=True)
        expected=world.expected()
        measurement=decide(state)
        raw=measurement['raw']
        try:
            route=parse_output(raw).route
        except (ValueError,TypeError):
            route='invalid'
        row={'step':step+1,'state':state.model_dump(),'expected':expected,
             **measurement,'route':route,'schema_valid':route!='invalid',
             'allowed':route in allowed_routes(state),'correct_route':route==expected,
             'premature_finish':route=='finish' and expected!='finish'}
        if route=='invalid':
            row['event']={'status':'invalid_json'}
            trace.append(row)
            reason='invalid_output'
            break
        event=world.execute(route)
        row['event']=event
        trace.append(row)
        if world.terminal:
            reason='finish' if route=='finish' else 'invalid_route'
            break
    completed=world.complete() and reason=='finish' and not world.violation
    blocked_ok=(task.expected_blocked and reason=='finish' and trace[-1]['expected']=='finish'
                and not world.violation)
    return {'task_id':task.id,'expected_blocked':task.expected_blocked,
        'task_success':blocked_ok if task.expected_blocked else completed,
        'research_artifacts_complete':completed and not task.expected_blocked,
        'controlled_stop_correct':blocked_ok,'termination':reason,
        'constraint_violation':world.violation,'artifacts':sorted(world.artifacts),'trace':trace}


def aggregate(tasks):
    rows=[r for t in tasks for r in t['trace']]
    initial=[t['trace'][0] for t in tasks]
    tools=[r for r in rows if r['route'] in ('search','download_pdf','answer','compare')]
    research=[t for t in tasks if not t['expected_blocked']]
    stops=[t for t in tasks if t['expected_blocked']]
    ratio=lambda items,p:sum(p(x) for x in items)/len(items) if items else None
    return {'tasks':len(tasks),'layer1':{
        'fixed_initial_count':len(initial),'fixed_initial_accuracy':ratio(initial,lambda x:x['correct_route']),
        'visited_decisions':len(rows),'visited_route_accuracy':ratio(rows,lambda x:x['correct_route']),
        'schema_rate':ratio(rows,lambda x:x['schema_valid']),
        'allowed_rate':ratio(rows,lambda x:x['allowed']),
        'premature_finishes':sum(r['premature_finish'] for r in rows),
        'unnecessary_clarifications':sum(r['route']=='clarify' and r['expected']!='clarify' for r in rows),
        'note':'Fixed initial states are directly paired; later visited states/denominators differ by policy.'},
        'layer2':{'simulated_tool_calls':len(tools),
            'successful_calls':sum(r['event']['status']=='success' for r in tools),
            'injected_download_failures':sum(r['event']['status']=='injected_download_failure' for r in tools),
            'no_progress_calls':sum(not r['event'].get('progress',False) for r in tools),
            'argument_or_evidence_failures':sum(r['event']['status'] in ('no_target_argument','missing_target_evidence') for r in tools),
            'constraint_violating_tasks':sum(t['constraint_violation'] for t in tasks),
            'parameter_generation_accuracy':None,'retrieval_relevance':None,
            'note':'Shared ideal parameter bindings; deterministic mock search/PDF/analysis receipts, not production tools.'},
        'layer3':{'scenario_success_rate':ratio(tasks,lambda x:x['task_success']),
            'scenario_success_count':sum(t['task_success'] for t in tasks),
            'research_task_count':len(research),'research_completion_rate':ratio(research,lambda x:x['task_success']),
            'control_task_count':len(stops),'controlled_stop_rate':ratio(stops,lambda x:x['task_success']),
            'answer_quality':None,'real_evidence_faithfulness':None,'llm_judge':None,
            'note':'Artifact completion in simulator only; no actual paper content generation or semantic grading.'},
        'cost':{'local_model_calls':len(rows),'cloud_calls':0,
            'input_tokens':sum(r.get('input_tokens',0) for r in rows),
            'output_tokens':sum(r.get('output_tokens',0) for r in rows),
            'model_seconds':sum(r.get('seconds',0) for r in rows),
            'decision_p50':percentile([r.get('seconds',0) for r in rows],.5),
            'decision_p95':percentile([r.get('seconds',0) for r in rows],.95)},'trajectories':tasks}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--training-run',type=Path,default=Path('outputs/router/formal-20261028-02'))
    parser.add_argument('--output-dir',type=Path,default=Path('outputs/router/layered-20261028-01'))
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch
    from transformers import AutoTokenizer, GenerationConfig
    from peft import PeftModel
    path=check_model(args.model_path)
    run=json.loads((args.training_run/'result.json').read_text())
    if run['status']!='complete':
        raise ValueError('Training incomplete')
    checkpoint=args.training_run/f"epoch-{run['selected_epoch']}"
    registration=json.loads((args.training_run/'preregistered.json').read_text())
    hashes={f:file_sha256(path/f) for f in FILES}
    if hashes!=registration['base_files']:
        raise ValueError('Wrong base checkpoint')
    tasks=suite()
    output=args.output_dir
    output.mkdir(parents=True,exist_ok=False)
    fixtures=[{**asdict(t),'initial':t.initial.model_dump()} for t in tasks]
    save(output/'fixtures.json',fixtures)
    save(output/'metadata.json',{'status':'started','simulation':True,'base_files':hashes,
        'adapter_directory':str(checkpoint/'adapter'),'fixture_sha256':digest(output/'fixtures.json'),
        'runner_sha256':digest(Path(__file__)),'simulator_sha256':digest(Path(__file__).with_name('task_suite.py')),
        'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        'decoding':{**GREEDY_OPTIONS,'max_new_tokens':32},'max_steps':12,
        'parameter_policy':'fixed ideal resolver shared by both models; no gold route passed into model or executor',
        'cloud_calls':0,'independent_review':'pending','not_real_world_completion':True})
    try:
        torch.manual_seed(20261028)
        tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        model=PeftModel.from_pretrained(load_base(path,False),checkpoint/'adapter',local_files_only=True)
        if adapter_hashes(model)!=json.loads((checkpoint/'tensor_hashes.json').read_text()):
            raise ValueError('Adapter mismatch')
        model.eval()
        model.config.use_cache=True
        decoding=GenerationConfig(max_new_tokens=32,do_sample=False,num_beams=1,
            pad_token_id=tokenizer.eos_token_id,eos_token_id=tokenizer.eos_token_id,
            bos_token_id=tokenizer.bos_token_id,repetition_penalty=1.0)
        model.generation_config=decoding
        def decide(state):
            prompt=tokenizer.apply_chat_template(model_messages(state),tokenize=False,add_generation_prompt=True)
            inputs=tokenizer(prompt,return_tensors='pt',add_special_tokens=False)
            n=inputs['input_ids'].shape[-1]
            if n>2048:
                raise ValueError('No truncation; state exceeds 2048 input tokens')
            inputs={k:v.to('cuda') for k,v in inputs.items()}
            torch.cuda.synchronize()
            start=time.perf_counter()
            with torch.inference_mode():
                generated=generate_greedy(model,inputs,decoding)[0,n:].tolist()
            torch.cuda.synchronize()
            return {'raw':tokenizer.decode(generated,skip_special_tokens=True),'token_ids':generated,
                'input_tokens':n,'output_tokens':len(generated),'seconds':time.perf_counter()-start,
                'hit_output_limit':len(generated)>=32}
        reports={}
        for mode in ('base','finetuned'):
            with model.disable_adapter() if mode=='base' else nullcontext():
                decide(tasks[0].initial)
                trajectories=[]
                for task in tasks:
                    trajectory=run_task(task,decide)
                    trajectories.append(trajectory)
                    with (output/f'{mode}-trajectories.jsonl').open('a',encoding='utf-8') as f:
                        f.write(json.dumps(trajectory,ensure_ascii=False)+'\n')
                    print(json.dumps({'mode':mode,'task':task.id,'success':trajectory['task_success'],
                        'steps':len(trajectory['trace'])}),flush=True)
                reports[mode]=aggregate(trajectories)
                save(output/f'{mode}-report.json',reports[mode])
        comparison={'status':'complete','simulation':True,
            'base':{k:v for k,v in reports['base'].items() if k!='trajectories'},
            'finetuned':{k:v for k,v in reports['finetuned'].items() if k!='trajectories'},
            'paired_tasks':[{'id':a['task_id'],'base_success':a['task_success'],'finetuned_success':b['task_success']}
                for a,b in zip(reports['base']['trajectories'],reports['finetuned']['trajectories'],strict=True)],
            'limitations':['Synthetic task family reuse; not independent gold review',
                          'Tool parameters and content are simulated, not generated',
                          'No live search/PDF/RAG/answer quality evaluation',
                          'No production integration or training']}
        save(output/'comparison.json',comparison)
        meta=json.loads((output/'metadata.json').read_text())
        meta['status']='complete'
        save(output/'metadata.json',meta)
        print(json.dumps({'status':'complete','output':str(output)}),flush=True)
    except BaseException as exc:
        save(output/'incomplete.json',{'status':'incomplete','error_type':type(exc).__name__})
        raise


if __name__=='__main__':
    main()
