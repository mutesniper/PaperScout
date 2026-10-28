"""资源受限的 QLoRA 工程冒烟测试，不进行验证集或测试集评分，也不部署。"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import time
from pathlib import Path

from .contract import model_messages
from .dataset import ROOT, digest, read_examples
from .local_baseline import FILES, check_model, file_sha256, generate_greedy
from .targeted_data import DEST, smoke_examples


def encode_supervised(tokenizer,example,maximum=512):
    messages=model_messages(example.state)
    expected=example.expected.model_dump_json()
    prompt=tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True)
    full=tokenizer.apply_chat_template(messages+[{'role':'assistant','content':expected}],
        tokenize=True,add_generation_prompt=False)
    boundary=len(prompt)
    if full[:boundary]!=prompt:
        raise ValueError('Chat template prefix is not stable; cannot construct assistant mask')
    try:
        end=full.index(tokenizer.eos_token_id,boundary)+1
    except ValueError as exc:
        raise ValueError('Assistant termination token missing') from exc
    # 忽略 EOS 后的模板空白，不截断提示词或正文。
    full=full[:end]
    if len(full)>maximum:
        raise ValueError(f'{example.id}: {len(full)} tokens exceeds {maximum}; no truncation')
    if tokenizer.decode(full[boundary:end-1],skip_special_tokens=False)!=expected:
        raise ValueError('Supervised content must be exactly route JSON')
    labels=[-100]*boundary+full[boundary:]
    if not full[boundary:] or any(v!=-100 for v in labels[:boundary]):
        raise ValueError('Invalid assistant-only labels')
    return {'id':example.id,'input_ids':full,'labels':labels,'prompt_length':boundary,
        'supervised_tokens':end-boundary}


def adapter_hashes(model):
    from peft import get_peft_model_state_dict
    return {name:hashlib.sha256(value.detach().cpu().contiguous().numpy().tobytes()).hexdigest()
        for name,value in get_peft_model_state_dict(model).items()}


def load_base(path,training):
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig
    from peft import prepare_model_for_kbit_training
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('CUDA and BF16 required; no CPU/cloud fallback')
    config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
        bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16)
    model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,trust_remote_code=False,
        torch_dtype=torch.bfloat16,quantization_config=config,device_map={'':0})
    model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=training,
        gradient_checkpointing_kwargs={'use_reentrant':False})
    model.config.use_cache=False
    return model


def probes(model,tokenizer,examples):
    import torch
    from transformers import GenerationConfig
    model.eval()
    model.gradient_checkpointing_disable()
    model.config.use_cache=True
    config=GenerationConfig(do_sample=False,num_beams=1,max_new_tokens=32,
        eos_token_id=tokenizer.eos_token_id,pad_token_id=tokenizer.eos_token_id,
        bos_token_id=tokenizer.bos_token_id,repetition_penalty=1.0)
    model.generation_config=config
    results=[]
    for e in examples:
        prompt=tokenizer.apply_chat_template(model_messages(e.state),tokenize=False,add_generation_prompt=True)
        inputs=tokenizer(prompt,return_tensors='pt',add_special_tokens=False)
        inputs={k:v.to('cuda:0') for k,v in inputs.items()}
        with torch.inference_mode():
            generated=generate_greedy(model,inputs,config)
        tokens=generated[0,inputs['input_ids'].shape[-1]:].tolist()
        results.append({'id':e.id,'token_ids':tokens,'raw':tokenizer.decode(tokens,skip_special_tokens=True)})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--mode',choices=['inspect','train','reload'],default='inspect')
    parser.add_argument('--output-dir',type=Path,default=Path('outputs/router/qlora-smoke-20261028-01'))
    parser.add_argument('--steps',type=int,default=12)
    parser.add_argument('--max-length',type=int,default=512)
    args=parser.parse_args()
    if not 1<=args.steps<=20 or args.max_length<1:
        parser.error('Smoke is limited to 1–20 updates and a positive context budget')
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    path=check_model(args.model_path)
    import torch
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    rows=read_examples(DEST/'train.jsonl')
    encoded={e.id:encode_supervised(tokenizer,e,args.max_length) for e in rows}
    summary={'samples':len(rows),'max_total_tokens':max(len(v['input_ids']) for v in encoded.values()),
        'min_total_tokens':min(len(v['input_ids']) for v in encoded.values()),
        'min_supervised_tokens':min(v['supervised_tokens'] for v in encoded.values()),
        'max_supervised_tokens':max(v['supervised_tokens'] for v in encoded.values()),
        'mask':'only assistant JSON + EOS; all system/user tokens are -100','truncated':0}
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if args.mode=='inspect':
        return
    output=args.output_dir
    if args.mode=='reload':
        from peft import PeftModel
        run=json.loads((output/'training_report.json').read_text(encoding='utf-8'))
        if run['status']!='training_complete' or run['data_sha256']!=digest(DEST/'train.jsonl'):
            raise ValueError('Incomplete training or changed training data')
        if {name:file_sha256(path/name) for name in FILES}!=run['base_files_sha256']:
            raise ValueError('Base checkpoint changed')
        base=load_base(path,False)
        model=PeftModel.from_pretrained(base,output/'adapter',is_trainable=False,local_files_only=True)
        hashes=adapter_hashes(model)
        if hashes!=run['adapter_tensor_sha256']:
            raise ValueError('Reloaded adapter tensor hashes differ')
        selected={e.id:e for e in rows}
        actual=probes(model,tokenizer,[selected[r['id']] for r in run['probes_before_reload']])
        matches=actual==run['probes_before_reload']
        result={'status':'passed' if matches else 'failed','adapter_tensors_match':True,
            'greedy_probe_outputs_match':matches,'probes_after_reload':actual,
            'not_quality_evaluation':True,'heldout_model_calls':0}
        (output/'reload_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if not matches:
            raise RuntimeError('Independent reload inference differs')
        return
    from peft import LoraConfig, TaskType, get_peft_model
    torch.manual_seed(20261028)
    output.mkdir(parents=True,exist_ok=False)
    selected=smoke_examples(rows)
    # 纳入实际最长输入，不只选用便于运行的短样例。
    longest=max(rows,key=lambda e:len(encoded[e.id]['input_ids']))
    if longest.id not in {e.id for e in selected}:
        index=next(i for i,e in enumerate(selected) if e.expected.route==longest.expected.route)
        selected[index]=longest
    config={'steps':args.steps,'batch_size':1,'gradient_accumulation':4,'max_length':args.max_length,
        'learning_rate':2e-5,'rank':8,'alpha':16,'dropout':.05,'seed':20261028,
        'target_modules':['q_proj','k_proj','v_proj','o_proj'],'gradient_checkpointing':True,
        'optimizer':'torch.optim.AdamW','weight_decay':0.0,'max_grad_norm':1.0,
        'precision':'NF4 double quantization, BF16 autocast; PEFT kbit preparation',
        'max_updates_policy':'engineering smoke only; never resume this adapter as formal training'}
    report={'status':'started','config':config,'supervision':summary,
        'data_sha256':digest(DEST/'train.jsonl'),'selected_ids':[e.id for e in selected],
        'heldout_hashes':{s:digest(ROOT/f'{s}.jsonl') for s in ('validation','test_candidate')},
        'base_files_sha256':{name:file_sha256(path/name) for name in FILES},
        'versions':{p:importlib.metadata.version(p) for p in ('torch','transformers','peft','bitsandbytes')},
        'script_sha256':digest(Path(__file__)),'heldout_model_calls':0,'cloud_calls':0,
        'not_quality_evaluation':True}
    (output/'run_config.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        base=load_base(path,True)
        model=get_peft_model(base,LoraConfig(task_type=TaskType.CAUSAL_LM,r=8,lora_alpha=16,
            lora_dropout=.05,bias='none',target_modules=config['target_modules']))
        trainable=[(n,p) for n,p in model.named_parameters() if p.requires_grad]
        if not trainable or any('lora_' not in n for n,p in trainable):
            raise RuntimeError('Only LoRA parameters may be trainable')
        report['trainable_parameters']=sum(p.numel() for n,p in trainable)
        initial={n:p.detach().cpu().clone() for n,p in trainable}
        optimizer=torch.optim.AdamW([p for n,p in trainable],lr=2e-5,weight_decay=0.0)
        model.train()
        torch.cuda.reset_peak_memory_stats()
        started=time.perf_counter()
        log=[]
        for step in range(args.steps):
            optimizer.zero_grad(set_to_none=True)
            losses=[]
            for micro in range(4):
                example=selected[(step*4+micro)%len(selected)]
                item=encoded[example.id]
                inputs=torch.tensor([item['input_ids']],device='cuda',dtype=torch.long)
                labels=torch.tensor([item['labels']],device='cuda',dtype=torch.long)
                with torch.autocast('cuda',dtype=torch.bfloat16):
                    loss=model(input_ids=inputs,attention_mask=torch.ones_like(inputs),labels=labels).loss
                if not torch.isfinite(loss).item():
                    raise RuntimeError('Non-finite training loss')
                losses.append(float(loss.detach()))
                (loss/4).backward()
            norm=torch.nn.utils.clip_grad_norm_([p for n,p in trainable],1.0,error_if_nonfinite=True)
            if not math.isfinite(float(norm)) or float(norm)<=0:
                raise RuntimeError('Expected nonzero finite adapter gradient')
            optimizer.step()
            row={'step':step+1,'mean_loss':sum(losses)/4,'gradient_norm_before_clip':float(norm),
                'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
                'peak_reserved_bytes':torch.cuda.max_memory_reserved()}
            log.append(row)
            with (output/'steps.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row)+'\n')
            print(json.dumps(row),flush=True)
        report['training_seconds']=time.perf_counter()-started
        report['max_adapter_weight_change']=max(float((p.detach().cpu()-initial[n]).abs().max()) for n,p in trainable)
        if report['max_adapter_weight_change']<=0:
            raise RuntimeError('Adapter weights did not change')
        report.update({'status':'training_complete','completed_updates':len(log),
            'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
            'first_step_loss':log[0]['mean_loss'],'last_step_loss':log[-1]['mean_loss']})
        model.save_pretrained(output/'adapter',safe_serialization=True)
        report['adapter_tensor_sha256']=adapter_hashes(model)
        probe_rows=[longest,next(e for e in selected if e.expected.route=='clarify')]
        report['probes_before_reload']=probes(model,tokenizer,probe_rows)
        (output/'training_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('status','completed_updates','training_seconds','peak_reserved_bytes')},ensure_ascii=False),flush=True)
    except BaseException as exc:
        (output/'incomplete.json').write_text(json.dumps({'status':'incomplete',
            'error_type':type(exc).__name__,'not_quality_evaluation':True}),encoding='utf-8')
        raise


if __name__=='__main__':
    main()
