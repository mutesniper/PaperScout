"""从已发布适配器进行一次保守续训，固定两轮并保存独立候选。"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import random
import time
from pathlib import Path

from .conservative_data import DEST
from .contract import SYSTEM_PROMPT
from .dataset import ROOT, digest, read_examples
from .formal_train import evaluate, write
from .local_baseline import FILES, check_model
from .progression_data import DEST as PREVIOUS
from .qlora_smoke import adapter_hashes, encode_supervised, load_base

DEFAULT_ADAPTER = Path('outputs/router/formal-20261028-03/epoch-3/adapter')
CONFIG = {'epochs':2, 'seed':20261028, 'peak_learning_rate':8e-6,
    'end_learning_rate':2e-6, 'warmup_updates':8, 'gradient_accumulation':4,
    'batch_size':1, 'max_length':512, 'optimizer':'AdamW', 'weight_decay':0.0,
    'max_grad_norm':1.0, 'updates':150,
    'selection':'fewest previously correct validation regressions; allowed rate; accuracy; macro F1; earlier epoch',
    'cloud_calls':0, 'start':'published epoch-3 adapter, optimizer reset',
    'adoption':'no previously correct cases/tasks lost; no metric/safety regression; at least one gain',
    'test_role':'120 candidates scored once after selecting one checkpoint; no tuning after test'}


def lr_at(step):
    if step <= CONFIG['warmup_updates']:
        return CONFIG['peak_learning_rate'] * step / CONFIG['warmup_updates']
    ratio = (step-CONFIG['warmup_updates'])/(CONFIG['updates']-CONFIG['warmup_updates'])
    return CONFIG['end_learning_rate'] + (CONFIG['peak_learning_rate']-CONFIG['end_learning_rate'])*(1+math.cos(math.pi*ratio))/2


def regressions(reference, candidate):
    if [r['id'] for r in reference['rows']] != [r['id'] for r in candidate['rows']]:
        raise ValueError('对照样例必须完全一致且同序')
    return [a['id'] for a,b in zip(reference['rows'],candidate['rows'],strict=True)
            if a['acceptable'] and not b['acceptable']]


def selection_key(report, reference):
    return (-len(regressions(reference,report)),report['allowed_rate'],
            report['acceptable_accuracy'],report['macro_f1'],-report['epoch'])


def protected_files(adapter):
    return [ROOT/'validation.jsonl', ROOT/'test_candidate.jsonl', PREVIOUS/'train.jsonl',
            PREVIOUS/'probe.jsonl', Path(__file__).with_name('runtime_profile.json'),
            adapter/'adapter_model.safetensors', adapter/'adapter_config.json']


def hashes(paths):
    return {str(p):digest(p) for p in paths}


def assert_unchanged(registration):
    if hashes([Path(p) for p in registration['protected_files']]) != registration['protected_files']:
        raise ValueError('保护文件发生变化')
    if digest(DEST/'train.jsonl') != registration['train_sha256']:
        raise ValueError('登记后训练集发生变化')
    if hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() != registration['prompt_sha256']:
        raise ValueError('提示词发生变化')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--adapter-path',type=Path,default=DEFAULT_ADAPTER)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--stage',choices=['prepare','train','reload'],required=True)
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch
    from transformers import AutoTokenizer
    from peft import PeftModel
    model_path=check_model(args.model_path)
    tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True,trust_remote_code=False)
    out=args.output_dir
    if args.stage=='prepare':
        train=read_examples(DEST/'train.jsonl')
        encoded=[encode_supervised(tokenizer,e,CONFIG['max_length']) for e in train]
        profile=json.loads(Path(__file__).with_name('runtime_profile.json').read_text())
        base_files={f:digest(model_path/f) for f in FILES}
        if base_files!=profile['base_files']:
            raise ValueError('基座与当前运行配置不一致')
        if digest(args.adapter_path/'adapter_model.safetensors') != '0eb66e6ea52df5075a59d65749e9cc6da8a43872247f2ebaa6354d563f1db458':
            raise ValueError('必须从已发布的最终适配器开始')
        if len(train)!=300 or len(train)*CONFIG['epochs']//4!=CONFIG['updates']:
            raise ValueError('训练数据与预定更新数不一致')
        # 登记固定的输入和代码哈希，测试候选只取哈希，不进行预测或选模。
        out.mkdir(parents=True,exist_ok=False)
        write(out/'preregistered.json',{**CONFIG,'base_files':base_files,
            'adapter_path':str(args.adapter_path),'train_count':len(train),
            'train_sha256':digest(DEST/'train.jsonl'),'max_training_tokens':max(len(x['input_ids']) for x in encoded),
            'protected_files':hashes(protected_files(args.adapter_path)),
            'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            'source_files':hashes(sorted(Path(__file__).parent.glob('*.py'))),
            'versions':{p:importlib.metadata.version(p) for p in ('torch','transformers','peft','bitsandbytes')}})
        print(json.dumps({'prepared':True,'count':len(train),'max_tokens':max(len(x['input_ids']) for x in encoded)}),flush=True)
        return
    reg=json.loads((out/'preregistered.json').read_text())
    assert_unchanged(reg)
    if reg['base_files']!={f:digest(model_path/f) for f in FILES}:
        raise ValueError('基座哈希发生变化')
    if reg['source_files']!=hashes([Path(p) for p in reg['source_files']]):
        raise ValueError('预登记后训练或评测代码发生变化')
    validation=read_examples(ROOT/'validation.jsonl')
    if args.stage=='reload':
        result=json.loads((out/'result.json').read_text())
        if result['status']!='complete':
            raise ValueError('训练尚未完成')
        checkpoint=out/f"epoch-{result['selected_epoch']}"
        model=PeftModel.from_pretrained(load_base(model_path,False),checkpoint/'adapter',local_files_only=True)
        tensor_match=adapter_hashes(model)==json.loads((checkpoint/'tensor_hashes.json').read_text())
        report=evaluate(model,tokenizer,validation,out/'reloaded-validation.json')
        original=json.loads((checkpoint/'validation.json').read_text())
        output_match=[r['raw'] for r in report['rows']]==[r['raw'] for r in original['rows']]
        write(out/'reload_check.json',{'tensor_match':tensor_match,'all_outputs_match':output_match})
        assert tensor_match and output_match
        assert_unchanged(reg)
        print(json.dumps({'reload':'passed'}),flush=True)
        return
    # 防止覆盖已有训练或从失败的中间状态悄悄续跑。
    (out/'training_started').open('x').close()
    try:
        torch.manual_seed(CONFIG['seed'])
        model=PeftModel.from_pretrained(load_base(model_path,True),args.adapter_path,
                                       is_trainable=True,local_files_only=True)
        baseline=evaluate(model,tokenizer,validation,out/'incumbent-validation.json')
        historical=json.loads((DEFAULT_ADAPTER.parent/'validation.json').read_text())
        if [r['raw'] for r in baseline['rows']] != [r['raw'] for r in historical['rows']]:
            raise ValueError('当前版复测与历史输出不同，先排查环境再训练')
        print(json.dumps({'incumbent_accuracy':baseline['acceptable_accuracy']}),flush=True)
        train=read_examples(DEST/'train.jsonl')
        encoded={e.id:encode_supervised(tokenizer,e,CONFIG['max_length']) for e in train}
        params=[p for n,p in model.named_parameters() if p.requires_grad]
        assert params and all('lora_' in n for n,p in model.named_parameters() if p.requires_grad)
        optimizer=torch.optim.AdamW(params,lr=CONFIG['peak_learning_rate'],weight_decay=0)
        reports=[]
        update=0
        for epoch in range(1,CONFIG['epochs']+1):
            folder=out/f'epoch-{epoch}'
            folder.mkdir()
            order=list(train)
            random.Random(CONFIG['seed']+epoch).shuffle(order)
            write(folder/'order.json',[e.id for e in order])
            model.train()
            model.config.use_cache=False
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
            torch.cuda.reset_peak_memory_stats()
            started=time.perf_counter()
            losses=[]
            for offset in range(0,len(order),4):
                update+=1
                lr=lr_at(update)
                for group in optimizer.param_groups:group['lr']=lr
                optimizer.zero_grad(set_to_none=True)
                values=[]
                for e in order[offset:offset+4]:
                    item=encoded[e.id]
                    tokens=torch.tensor([item['input_ids']],device='cuda')
                    labels=torch.tensor([item['labels']],device='cuda')
                    with torch.autocast('cuda',dtype=torch.bfloat16):
                        loss=model(input_ids=tokens,attention_mask=torch.ones_like(tokens),labels=labels).loss
                    if not torch.isfinite(loss):raise RuntimeError('损失出现非有限数值')
                    values.append(float(loss.detach()))
                    (loss/4).backward()
                norm=torch.nn.utils.clip_grad_norm_(params,1.0,error_if_nonfinite=True)
                optimizer.step()
                row={'epoch':epoch,'update':update,'lr':lr,'loss':sum(values)/4,'gradient_norm':float(norm)}
                losses.append(row)
                with (folder/'steps.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(row)+'\n')
                if update%10==0:print(json.dumps(row),flush=True)
            write(folder/'training.json',{'seconds':time.perf_counter()-started,
                'mean_loss':sum(r['loss'] for r in losses)/len(losses),
                'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
                'peak_reserved_bytes':torch.cuda.max_memory_reserved(),'updates':len(losses)})
            model.save_pretrained(folder/'adapter',safe_serialization=True)
            write(folder/'tensor_hashes.json',adapter_hashes(model))
            report=evaluate(model,tokenizer,validation,folder/'validation.json')
            report['epoch']=epoch
            reports.append(report)
            print(json.dumps({'epoch':epoch,'accuracy':report['acceptable_accuracy'],
                'regressions':len(regressions(baseline,report)),'allowed_rate':report['allowed_rate']}),flush=True)
        selected=max(reports,key=lambda r:selection_key(r,baseline))
        write(out/'result.json',{'status':'complete','selected_epoch':selected['epoch'],
            'selected_accuracy':selected['acceptable_accuracy'],'validation_regressions':regressions(baseline,selected),
            'selection':CONFIG['selection'],'completed_updates':update,'adopted':False})
        assert_unchanged(reg)
    except BaseException as exc:
        write(out/'incomplete.json',{'error_type':type(exc).__name__})
        raise


if __name__=='__main__':
    main()
