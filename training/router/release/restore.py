"""无需项目仓库即可校验并加载 PaperScout 工具路由适配器。"""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify(root, checksums):
    for name, expected in checksums.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f'Missing or invalid file: {name}')
        if sha256(path) != expected:
            raise ValueError(f'SHA256 mismatch: {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-model', type=Path, help='Directory containing the original base model')
    parser.add_argument('--download', action='store_true', help='Download the pinned base revision to --base-model')
    parser.add_argument('--verify-only', action='store_true', help='Verify package, and base if provided; do not use GPU')
    parser.add_argument('--self-test', action='store_true', help='Compare all 36 saved reference outputs')
    parser.add_argument('--input', type=Path, help='A task-state JSON file; defaults to example_state.json')
    args = parser.parse_args()
    verify(ROOT, json.loads((ROOT / 'checksums.json').read_text(encoding='utf-8')))
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    print('Package checksums: OK', flush=True)
    if args.download:
        if args.base_model is None:
            parser.error('--download requires --base-model')
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=manifest['base_model']['id'], revision=manifest['base_model']['revision'],
                          local_dir=str(args.base_model), allow_patterns=list(manifest['base_files']))
    if args.base_model:
        verify(args.base_model, manifest['base_files'])
        print('Base model checksums: OK', flush=True)
    if args.verify_only:
        return
    if not args.base_model:
        parser.error('Provide --base-model; use --download to obtain the pinned model first')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, GenerationConfig
    from peft import PeftModel, prepare_model_for_kbit_training
    from router_contract import SYSTEM_PROMPT, State, model_messages, parse_output, allowed_routes
    if hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() != manifest['prompt_sha256']:
        raise ValueError('Unexpected routing prompt')
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('This recovery profile requires an NVIDIA GPU supporting BF16 (tested in WSL2)')
    torch.manual_seed(20261028)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, local_files_only=True, trust_remote_code=False)
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                     bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    base = AutoModelForCausalLM.from_pretrained(args.base_model, local_files_only=True, trust_remote_code=False,
                                               torch_dtype=torch.bfloat16, quantization_config=quantization,
                                               device_map={'': 0})
    # 非量化参数的数据类型与原始评测保持一致。
    base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=False)
    model = PeftModel.from_pretrained(base, ROOT / 'adapter', local_files_only=True)
    model.eval()
    model.config.use_cache = True
    decoding = GenerationConfig(max_new_tokens=32, do_sample=False, num_beams=1,
                                pad_token_id=tokenizer.eos_token_id, eos_token_id=tokenizer.eos_token_id,
                                bos_token_id=tokenizer.bos_token_id, repetition_penalty=1.0)
    model.generation_config = decoding

    def infer(value):
        state = State.model_validate(value)
        prompt = tokenizer.apply_chat_template(model_messages(state), tokenize=False, add_generation_prompt=True)
        tokens = tokenizer(prompt, return_tensors='pt', add_special_tokens=False, truncation=False)
        count = tokens['input_ids'].shape[-1]
        if count > 2048:
            raise ValueError('Input exceeds 2048 tokens; no automatic truncation')
        with torch.inference_mode():
            generated = model.generate(**{k: v.to('cuda') for k, v in tokens.items()},
                generation_config=decoding, do_sample=False, num_beams=1, temperature=1.0, top_p=1.0, top_k=50)
        raw = tokenizer.decode(generated[0, count:].tolist(), skip_special_tokens=True)
        return state, raw

    if args.self_test:
        cases = [json.loads(line) for line in (ROOT / 'recovery_cases.jsonl').read_text(encoding='utf-8').splitlines()]
        mismatches = []
        for i, case in enumerate(cases, 1):
            _, raw = infer(case['state'])
            if raw != case['reference_raw']:
                mismatches.append({'id': case['id'], 'reference': case['reference_raw'], 'actual': raw})
            print(json.dumps({'case': i, 'matches_reference': raw == case['reference_raw']}), flush=True)
        print(json.dumps({'total':len(cases), 'matched':len(cases)-len(mismatches),
                          'mismatches':mismatches, 'note':'Recovery consistency, not accuracy against task labels.'},
                         ensure_ascii=False), flush=True)
        if mismatches:
            raise SystemExit(1)
    else:
        path = args.input or ROOT / 'example_state.json'
        state, raw = infer(json.loads(path.read_text(encoding='utf-8')))
        decision = parse_output(raw)
        print(json.dumps({'raw':raw, 'route':decision.route, 'allowed':decision.route in allowed_routes(state)},
                         ensure_ascii=False))


if __name__ == '__main__':
    main()
