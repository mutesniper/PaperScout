"""Offline-only original Qwen3 routing evaluation. No adapter or cloud fallback.

ML dependencies load lazily: preflight and unit tests use the backend venv.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .contract import SYSTEM_PROMPT, model_messages
from .dataset import ROOT, digest, read_examples
from .evaluate import score

MODEL_ID = 'Qwen/Qwen3-4B-Instruct-2507'
REVISION = 'cdbee75f17c01a7cc42f958dc650907174af0554'
FILES = ('config.json','generation_config.json','tokenizer.json','tokenizer_config.json',
         'merges.txt','vocab.json','model.safetensors.index.json',
         'model-00001-of-00003.safetensors','model-00002-of-00003.safetensors',
         'model-00003-of-00003.safetensors')
GREEDY_OPTIONS = {'do_sample':False,'num_beams':1,'temperature':1.0,'top_p':1.0,'top_k':50}


def generate_greedy(model,tokens,decoding):
    # Explicit generate kwargs override checkpoint defaults in Transformers 4.51.
    return model.generate(**tokens,generation_config=decoding,**GREEDY_OPTIONS)


def check_model(path):
    path = Path(path).resolve()
    missing = [f for f in FILES if not (path/f).is_file() or (path/f).stat().st_size==0]
    if missing:
        raise ValueError('Model files missing: ' + ', '.join(missing))
    if (path/'adapter_config.json').exists():
        raise ValueError('Baseline must not load an adapter directory')
    config = json.loads((path/'config.json').read_text(encoding='utf-8'))
    if any(config.get(k)!=v for k,v in {'model_type':'qwen3','hidden_size':2560,
        'num_hidden_layers':36,'intermediate_size':9728,'vocab_size':151936}.items()):
        raise ValueError('Expected original Qwen3 checkpoint, not a different model')
    index = json.loads((path/'model.safetensors.index.json').read_text(encoding='utf-8'))
    shards = set(index.get('weight_map',{}).values())
    if shards != set(FILES[-3:]):
        raise ValueError('Unexpected or incomplete checkpoint shard index')
    # Catch saved HTML/LFS pointer files before trying to allocate GPU memory.
    for name in FILES[-3:]:
        with (path/name).open('rb') as handle:
            prefix = handle.read(128).lstrip().lower()
        if prefix.startswith((b'<',b'version https://git-lfs')):
            raise ValueError(f'{name} is a webpage/LFS pointer, not a weight shard')
    return path


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def encode_inputs(tokenizer, examples, maximum, new_tokens):
    encoded = []
    for example in examples:
        prompt = tokenizer.apply_chat_template(model_messages(example.state),
            tokenize=False,add_generation_prompt=True)
        tokens = tokenizer(prompt,return_tensors='pt',add_special_tokens=False,truncation=False)
        length = tokens['input_ids'].shape[-1]
        if length > maximum:
            raise ValueError(f'{example.id}: {length} input tokens > {maximum}; no truncation allowed')
        encoded.append((tokens,length))
    if maximum<=0 or new_tokens<=0:
        raise ValueError('Token budgets must be positive')
    return encoded


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values)-1)*fraction
    lo = int(pos)
    return values[lo] + (values[min(lo+1,len(values)-1)]-values[lo])*(pos-lo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--split',choices=['train','validation'],default='validation')
    parser.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--tokenize-only',action='store_true')
    parser.add_argument('--max-input-tokens',type=int,default=2048)
    parser.add_argument('--max-new-tokens',type=int,default=32)
    parser.add_argument('--output-root',type=Path,default=Path('outputs/router'))
    args = parser.parse_args()
    try:
        path = check_model(args.model_path)
    except ValueError as exc:
        parser.exit(2,str(exc)+'\nNo downloads or model calls were performed.\n')
    if args.preflight_only:
        print('Checkpoint file preflight passed; inference has not run.')
        return
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    try:
        from transformers import AutoTokenizer
    except ImportError:
        parser.exit(2,'Missing isolated inference dependencies; see BASELINE.md.\n')
    examples = read_examples(ROOT/f'{args.split}.jsonl')
    tokenizer = AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    encoded = encode_inputs(tokenizer,examples,args.max_input_tokens,args.max_new_tokens)
    lengths = [n for _,n in encoded]
    length_report = {'count':len(lengths),'minimum':min(lengths),'maximum':max(lengths),
        'p50':percentile(lengths,.5),'p95':percentile(lengths,.95),'truncated':0}
    print(json.dumps({'token_lengths':length_report},ensure_ascii=False),flush=True)
    if args.tokenize_only:
        return
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig, GenerationConfig
    if not torch.cuda.is_available():
        parser.exit(2,'CUDA unavailable; no implicit CPU/cloud fallback.\n')
    if not torch.cuda.is_bf16_supported():
        parser.exit(2,'Configured BF16 compute is unavailable.\n')
    config = json.loads((path/'config.json').read_text(encoding='utf-8'))
    if max(lengths)+args.max_new_tokens > config.get('max_position_embeddings',0):
        parser.exit(2,'Requested context exceeds model configuration.\n')
    output = args.output_root / ('base-'+args.split+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True,exist_ok=False)
    metadata = {'model':MODEL_ID,'requested_revision':REVISION,
        'provenance':'Local file hashes recorded; revision must be verified against download source',
        'split':args.split,'dataset_sha256':digest(ROOT/f'{args.split}.jsonl'),
        'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        'files':{f:file_sha256(path/f) for f in FILES},
        'versions':{p:importlib.metadata.version(p) for p in ('torch','transformers','accelerate','bitsandbytes')},
        'quantization':'NF4 double quantization, BF16 compute', 'adapter':None,
        'decoding':{**GREEDY_OPTIONS,'max_new_tokens':args.max_new_tokens},
        'runner_sha256':digest(Path(__file__)),
        'token_lengths':length_report,'gpu':torch.cuda.get_device_name(0),
        'cloud_calls':0,'status':'started','requested_count':len(examples)}
    (output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    torch.manual_seed(20261028)
    predictions, timings, outputs = [], [], []
    try:
        quantization = BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16)
        model = AutoModelForCausalLM.from_pretrained(path,local_files_only=True,trust_remote_code=False,
            torch_dtype=torch.bfloat16,quantization_config=quantization,device_map={'':0})
        model.eval()
        decoding = GenerationConfig(do_sample=False,num_beams=1,max_new_tokens=args.max_new_tokens,
            pad_token_id=tokenizer.eos_token_id,eos_token_id=tokenizer.eos_token_id,
            bos_token_id=tokenizer.bos_token_id,repetition_penalty=1.0)
        model.generation_config = decoding
        def infer(tokens):
            tokens = {key:value.to('cuda:0') for key,value in tokens.items()}
            with torch.inference_mode():
                return generate_greedy(model,tokens,decoding)
        # Warm up on an unlabelled input, excluded from scoring and timing.
        infer(encoded[0][0])
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        for example,(tokens,length) in zip(examples,encoded,strict=True):
            torch.cuda.synchronize()
            started = time.perf_counter()
            generated = infer(tokens)
            torch.cuda.synchronize()
            elapsed = time.perf_counter()-started
            generated_ids = generated[0,length:].tolist()
            raw = tokenizer.decode(generated_ids,skip_special_tokens=True)
            row = {'id':example.id,'raw':raw,'input_tokens':length,'output_tokens':len(generated_ids),
                'seconds':elapsed,'hit_output_limit':len(generated_ids)>=args.max_new_tokens}
            predictions.append(row)
            outputs.append(raw)
            timings.append(elapsed)
            with (output/'predictions.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            print(f'{len(predictions)}/{len(examples)} {example.id}: {elapsed:.2f}s',flush=True)
        report = score(examples,outputs)
        metadata['status'] = 'complete'
        (output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        report.update({'status':'complete','metadata':metadata,'latency_p50':percentile(timings,.5),
            'latency_p95':percentile(timings,.95),'peak_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),
            'peak_gpu_reserved_bytes':torch.cuda.max_memory_reserved(),'measurements':predictions})
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':'complete','count':len(outputs),
            'acceptable_accuracy':report['acceptable_accuracy'],'report':str(output/'report.json')},ensure_ascii=False))
    except BaseException as exc:
        # Never present an interrupted subset as a completed baseline.
        (output/'incomplete.json').write_text(json.dumps({'status':'incomplete',
            'completed':len(predictions),'requested':len(examples),'error_type':type(exc).__name__},indent=2),encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
