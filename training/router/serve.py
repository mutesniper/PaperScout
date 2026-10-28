"""Local-only HTTP service for the final route adapter; no cloud/model downloads."""
import argparse
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .contract import State, SYSTEM_PROMPT, model_messages
from .local_baseline import check_model, file_sha256, generate_greedy
from .qlora_smoke import load_base

ADAPTER_SHA256 = '0eb66e6ea52df5075a59d65749e9cc6da8a43872247f2ebaa6354d563f1db458'
IDENTITY = {'model':'paperscout-router-v1.0.0','adapter_sha256':ADAPTER_SHA256,
            'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()}


def handler_for(predict):
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log task text or client addresses.

        def send_json(self, status, value):
            data = json.dumps(value,ensure_ascii=False).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self.send_json(200,{'ready':True,**IDENTITY}) if self.path == '/health' else self.send_json(404,{'error':'not_found'})

        def do_POST(self):
            if self.path != '/route':
                return self.send_json(404,{'error':'not_found'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 65536:
                    return self.send_json(413,{'error':'state_too_large'})
                self.connection.settimeout(10)
                state = State.model_validate(json.loads(self.rfile.read(length)))
            except (ValueError, OSError):
                return self.send_json(422,{'error':'invalid_state'})
            if not lock.acquire(blocking=False):
                return self.send_json(409,{'error':'busy'})
            try:
                raw = predict(state)
                self.send_json(200,{'raw':raw,**IDENTITY})
            except ValueError:
                self.send_json(422,{'error':'context_limit'})
            except Exception:
                self.send_json(503,{'error':'inference_failed'})
            finally:
                lock.release()
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,default=Path('outputs/router/models/Qwen3-4B-Instruct-2507'))
    parser.add_argument('--adapter-path',type=Path,default=Path('outputs/router/formal-20261028-03/epoch-3/adapter'))
    parser.add_argument('--port',type=int,default=8010)
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer, GenerationConfig
    base_path = check_model(args.model_path)
    if file_sha256(args.adapter_path/'adapter_model.safetensors') != ADAPTER_SHA256:
        raise ValueError('Expected the final epoch-3 adapter weights')
    # Use the pinned file hashes from the completed training run.
    registration = json.loads(Path(__file__).with_name('runtime_profile.json').read_text())
    config = json.loads((args.adapter_path/'adapter_config.json').read_text())
    for key in ('base_model_name_or_path','revision'):
        config.pop(key,None)
    if config != registration['adapter_config'] or IDENTITY['prompt_sha256'] != registration['prompt_sha256']:
        raise ValueError('Adapter configuration or prompt changed')
    if any(file_sha256(base_path/name) != digest for name,digest in registration['base_files'].items()):
        raise ValueError('Base files do not match the final training run')
    torch.manual_seed(20261028)
    tokenizer = AutoTokenizer.from_pretrained(base_path,local_files_only=True,trust_remote_code=False)
    model = PeftModel.from_pretrained(load_base(base_path,False),args.adapter_path,local_files_only=True)
    model.eval()
    model.config.use_cache=True
    decoding = GenerationConfig(max_new_tokens=32,do_sample=False,num_beams=1,
        pad_token_id=tokenizer.eos_token_id,eos_token_id=tokenizer.eos_token_id,
        bos_token_id=tokenizer.bos_token_id,repetition_penalty=1.0)
    model.generation_config=decoding
    def predict(state):
        prompt = tokenizer.apply_chat_template(model_messages(state),tokenize=False,add_generation_prompt=True)
        inputs = tokenizer(prompt,return_tensors='pt',add_special_tokens=False,truncation=False)
        n = inputs['input_ids'].shape[-1]
        if n > 2048:
            raise ValueError('State exceeds 2048 tokens; not truncated')
        with torch.inference_mode():
            tokens = generate_greedy(model,{k:v.to('cuda') for k,v in inputs.items()},decoding)[0,n:].tolist()
        return tokenizer.decode(tokens,skip_special_tokens=True)
    server = ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(predict))
    server.daemon_threads=True
    print(json.dumps({'ready':True,'url':f'http://127.0.0.1:{args.port}',**IDENTITY}),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
