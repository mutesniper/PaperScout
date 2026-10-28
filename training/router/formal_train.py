"""执行一组预先登记的三轮 QLoRA 训练，仅验证，不部署。"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import random
import time
from pathlib import Path

from .contract import SYSTEM_PROMPT
from .dataset import ROOT, digest, read_examples
from .evaluate import score
from .local_baseline import FILES, GREEDY_OPTIONS, check_model, encode_inputs, file_sha256, generate_greedy, percentile
from .qlora_smoke import adapter_hashes, encode_supervised, load_base
from .targeted_data import DEST
from .boundary_data import DEST as BOUNDARY_DEST
from .progression_data import DEST as PROGRESSION_DEST


CONFIG = {'epochs':3, 'learning_rate':2e-5, 'batch_size':1, 'gradient_accumulation':4,
          'rank':8, 'alpha':16, 'dropout':0.05, 'max_length':512, 'seed':20261028,
          'target_modules':['q_proj','k_proj','v_proj','o_proj'],
          'selection':'highest acceptable_accuracy, then macro_f1, then earliest epoch',
          'optimizer':'AdamW', 'weight_decay':0.0, 'max_grad_norm':1.0,
          'start':'original base, never smoke adapter', 'test_model_calls':0, 'cloud_calls':0}


def selection_key(report):
    return (report['acceptable_accuracy'], report['macro_f1'], -report['epoch'])


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def evaluate(model, tokenizer, examples, output, dataset_path=None):
    import torch
    from transformers import GenerationConfig
    model.eval()
    model.gradient_checkpointing_disable()
    model.config.use_cache=True
    decoding=GenerationConfig(max_new_tokens=32,do_sample=False,num_beams=1,
        pad_token_id=tokenizer.eos_token_id,eos_token_id=tokenizer.eos_token_id,
        bos_token_id=tokenizer.bos_token_id,repetition_penalty=1.0)
    model.generation_config=decoding
    encoded=encode_inputs(tokenizer,examples,512,32)
    def infer(tokens):
        with torch.inference_mode():
            return generate_greedy(model,{k:v.to('cuda') for k,v in tokens.items()},decoding)
    infer(encoded[0][0])
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    measurements=[]
    for e,(tokens,n) in zip(examples,encoded,strict=True):
        torch.cuda.synchronize()
        start=time.perf_counter()
        ids=infer(tokens)[0,n:].tolist()
        torch.cuda.synchronize()
        measurements.append({'id':e.id,'raw':tokenizer.decode(ids,skip_special_tokens=True),
            'seconds':time.perf_counter()-start,'input_tokens':n,'output_tokens':len(ids),
            'hit_output_limit':len(ids)>=32})
    report=score(examples,[r['raw'] for r in measurements])
    report.update({'measurements':measurements,'status':'complete',
        'dataset_sha256':digest(dataset_path or ROOT/'validation.jsonl'),
        'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        'decoding':{**GREEDY_OPTIONS,'max_new_tokens':32},
        'latency_p50':percentile([r['seconds'] for r in measurements],.5),
        'latency_p95':percentile([r['seconds'] for r in measurements],.95),
        'peak_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),
        'peak_gpu_reserved_bytes':torch.cuda.max_memory_reserved(),
        'memory_note':'in-process evaluation; trainer allocations may remain for epoch checkpoints'})
    write(output,report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,default=Path('outputs/router/formal-20261028-01'))
    parser.add_argument('--reload',action='store_true')
    parser.add_argument('--profile',choices=['targeted','boundary','progression'],default='targeted')
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch
    from transformers import AutoTokenizer
    from peft import LoraConfig, TaskType, get_peft_model, PeftModel
    path=check_model(args.model_path)
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    validation=read_examples(ROOT/'validation.jsonl')
    output=args.output_dir
    if args.reload:
        run=json.loads((output/'result.json').read_text(encoding='utf-8'))
        if run['status']!='complete':
            raise ValueError('Incomplete formal run')
        config=json.loads((output/'preregistered.json').read_text(encoding='utf-8'))
        if config['base_files']!={f:file_sha256(path/f) for f in FILES}:
            raise ValueError('Base weights changed')
        if config['validation_sha256']!=digest(ROOT/'validation.jsonl'):
            raise ValueError('Validation changed')
        checkpoint=output/f"epoch-{run['selected_epoch']}"
        prior=json.loads((checkpoint/'validation.json').read_text(encoding='utf-8'))
        model=PeftModel.from_pretrained(load_base(path,False),checkpoint/'adapter',local_files_only=True)
        if adapter_hashes(model)!=json.loads((checkpoint/'tensor_hashes.json').read_text()):
            raise ValueError('Adapter tensor mismatch')
        report=evaluate(model,tokenizer,validation,output/'selected-reloaded-validation.json')
        matches=[r['raw'] for r in report['rows']]==[r['raw'] for r in prior['rows']]
        write(output/'reload_check.json',{'all_36_outputs_match':matches,'adapter_tensors_match':True})
        if not matches:
            raise RuntimeError('Reload outputs differ')
        print(json.dumps({'reload':'passed','accuracy':report['acceptable_accuracy']}),flush=True)
        return
    output.mkdir(parents=True,exist_ok=False)
    train_path={'targeted':DEST,'boundary':BOUNDARY_DEST,'progression':PROGRESSION_DEST}[args.profile]/'train.jsonl'
    train=read_examples(train_path)
    encoded={e.id:encode_supervised(tokenizer,e,512) for e in train}
    if len(train)!=({'targeted':132,'boundary':168,'progression':228}[args.profile]) or len(train)%4:
        raise ValueError('Expected fixed profile corpus size')
    registration={**CONFIG,'profile':args.profile,'train_path':str(train_path),
        'train_count':len(train),'planned_updates':len(train)//4*CONFIG['epochs'],
        'train_sha256':digest(train_path),
        'validation_sha256':digest(ROOT/'validation.jsonl'),
        'test_candidate_sha256':digest(ROOT/'test_candidate.jsonl'),
        'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        'script_sha256':digest(Path(__file__)),
        'versions':{p:importlib.metadata.version(p) for p in ('torch','transformers','peft','bitsandbytes')},
        'base_files':{f:file_sha256(path/f) for f in FILES}}
    if args.profile == 'progression':
        registration['probe_sha256'] = digest(PROGRESSION_DEST/'probe.jsonl')
        registration['probe_role'] = 'Post-selection diagnostic only; never select checkpoints using probe.'
    write(output/'preregistered.json',registration)
    try:
        torch.manual_seed(CONFIG['seed'])
        base=load_base(path,True)
        # 采用相同低位量化准备流程和数据类型，重新评测未加载适配器的基座。
        baseline=evaluate(base,tokenizer,validation,output/'base-validation.json')
        print(json.dumps({'base_accuracy':baseline['acceptable_accuracy']}),flush=True)
        model=get_peft_model(base,LoraConfig(task_type=TaskType.CAUSAL_LM,r=8,lora_alpha=16,
            lora_dropout=.05,bias='none',target_modules=CONFIG['target_modules']))
        params=[p for n,p in model.named_parameters() if p.requires_grad]
        assert params and all('lora_' in n for n,p in model.named_parameters() if p.requires_grad)
        optimizer=torch.optim.AdamW(params,lr=CONFIG['learning_rate'],weight_decay=0)
        epochs=[]
        for epoch in range(1,4):
            folder=output/f'epoch-{epoch}'
            folder.mkdir()
            order=list(train)
            random.Random(CONFIG['seed']+epoch).shuffle(order)
            write(folder/'order.json',[e.id for e in order])
            model.train()
            model.config.use_cache=False
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
            torch.cuda.reset_peak_memory_stats()
            start=time.perf_counter()
            losses=[]
            for offset in range(0,len(order),4):
                optimizer.zero_grad(set_to_none=True)
                values=[]
                for e in order[offset:offset+4]:
                    item=encoded[e.id]
                    tokens=torch.tensor([item['input_ids']],device='cuda')
                    labels=torch.tensor([item['labels']],device='cuda')
                    with torch.autocast('cuda',dtype=torch.bfloat16):
                        loss=model(input_ids=tokens,attention_mask=torch.ones_like(tokens),labels=labels).loss
                    if not torch.isfinite(loss):
                        raise RuntimeError('Nonfinite loss')
                    values.append(float(loss.detach()))
                    (loss/4).backward()
                norm=torch.nn.utils.clip_grad_norm_(params,1.0,error_if_nonfinite=True)
                if float(norm)<=0:
                    raise RuntimeError('Missing gradient')
                optimizer.step()
                row={'epoch':epoch,'step':offset//4+1,'loss':sum(values)/4,'gradient_norm':float(norm)}
                losses.append(row)
                with (folder/'steps.jsonl').open('a') as stream:
                    stream.write(json.dumps(row)+'\n')
                if row['step']%5==0:
                    print(json.dumps(row),flush=True)
            stats={'training_seconds':time.perf_counter()-start,
                'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
                'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
                'mean_loss':sum(r['loss'] for r in losses)/len(losses),'updates':len(losses)}
            write(folder/'training.json',stats)
            model.save_pretrained(folder/'adapter',safe_serialization=True)
            write(folder/'tensor_hashes.json',adapter_hashes(model))
            report=evaluate(model,tokenizer,validation,folder/'validation.json')
            report['epoch']=epoch
            epochs.append(report)
            print(json.dumps({'epoch':epoch,'accuracy':report['acceptable_accuracy'],
                'macro_f1':report['macro_f1'],'allowed_rate':report['allowed_rate']}),flush=True)
        best=max(epochs,key=selection_key)
        write(output/'result.json',{'status':'complete','selected_epoch':best['epoch'],
            'selection':CONFIG['selection'],'completed_updates':len(train)//4*CONFIG['epochs'],'cloud_calls':0,'test_model_calls':0,
            'base_accuracy':baseline['acceptable_accuracy'],'selected_accuracy':best['acceptable_accuracy']})
    except BaseException as exc:
        write(output/'incomplete.json',{'status':'incomplete','error_type':type(exc).__name__})
        raise


if __name__=='__main__':
    main()
