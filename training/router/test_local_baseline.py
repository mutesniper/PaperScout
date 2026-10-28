import json
from types import SimpleNamespace

import pytest

from training.router.build_specification import cases
from training.router.local_baseline import FILES, check_model, encode_inputs, percentile, generate_greedy


def fake_checkpoint(path):
    for name in FILES:
        (path/name).write_bytes(b'placeholder')
    (path/'config.json').write_text(json.dumps({'model_type':'qwen3','hidden_size':2560,
        'num_hidden_layers':36,'intermediate_size':9728,'vocab_size':151936}),encoding='utf-8')
    (path/'model.safetensors.index.json').write_text(json.dumps({
        'weight_map':{str(i):f for i,f in enumerate(FILES[-3:])}}),encoding='utf-8')


def test_missing_model_fails_without_importing_training_dependencies(tmp_path):
    with pytest.raises(ValueError,match='files missing'):
        check_model(tmp_path)


def test_rejects_html_in_weight_download(tmp_path):
    fake_checkpoint(tmp_path)
    (tmp_path/FILES[-1]).write_text('<html>access denied</html>',encoding='utf-8')
    with pytest.raises(ValueError,match='webpage/LFS'):
        check_model(tmp_path)


def test_rejects_wrong_model_and_adapters(tmp_path):
    fake_checkpoint(tmp_path)
    (tmp_path/'adapter_config.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='adapter'):
        check_model(tmp_path)
    (tmp_path/'adapter_config.json').unlink()
    (tmp_path/'config.json').write_text('{"model_type":"qwen3","hidden_size":2048}',encoding='utf-8')
    with pytest.raises(ValueError,match='different model'):
        check_model(tmp_path)


class Tokenizer:
    def apply_chat_template(self,messages,**kwargs):
        assert [m['role'] for m in messages] == ['system','user']
        assert 'expected' not in json.loads(messages[1]['content'])
        return 'rendered prompt'

    def __call__(self,prompt,**kwargs):
        assert kwargs['truncation'] is False
        assert kwargs['add_special_tokens'] is False
        return {'input_ids':SimpleNamespace(shape=(1,600))}


def test_overlength_inputs_are_rejected_not_truncated():
    with pytest.raises(ValueError,match='no truncation'):
        encode_inputs(Tokenizer(),cases()[:1],512,32)
    assert encode_inputs(Tokenizer(),cases()[:1],2048,32)[0][1] == 600


def test_latency_percentiles():
    assert percentile([1,2,3],.5) == 2
    assert percentile([1,2,3],.95) == pytest.approx(2.9)
    assert percentile([],.5) is None


def test_generation_overrides_sampling_checkpoint_defaults():
    class Model:
        def generate(self,**kwargs):
            defaults = {'do_sample':True,'temperature':.7,'top_p':.8,'top_k':20}
            defaults.update(kwargs)
            return defaults
    effective = generate_greedy(Model(),{'input_ids':'tokens'},object())
    assert effective['do_sample'] is False
    assert effective['num_beams'] == 1
    assert effective['temperature'] == 1.0
    assert effective['top_p'] == 1.0
    assert effective['input_ids'] == 'tokens'
