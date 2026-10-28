"""Build a portable release from the final adapter, leaving training outputs intact."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from .contract import SYSTEM_PROMPT
from .dataset import ROOT, read_examples
from .local_baseline import MODEL_ID, REVISION, file_sha256

REPO = Path(__file__).resolve().parents[2]
RUN = REPO/'outputs/router/formal-20261028-03'
RELEASES = REPO/'outputs/router/releases'
NAME = 'paperscout-router-v1.0.0'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    result = read(RUN/'result.json')
    if result['status'] != 'complete' or result['selected_epoch'] != 3:
        raise ValueError('Final checkpoint is not the expected completed run')
    if not all(read(RUN/'reload_check.json').values()):
        raise ValueError('Independent reload has not passed')
    source = RUN/'epoch-3/adapter'
    registration = read(RUN/'preregistered.json')
    if hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() != registration['prompt_sha256']:
        raise ValueError('Routing prompt changed')
    folder = RELEASES/NAME
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'adapter').mkdir()
    shutil.copyfile(source/'adapter_model.safetensors',folder/'adapter/adapter_model.safetensors')
    config = read(source/'adapter_config.json')
    config['base_model_name_or_path'] = MODEL_ID
    config['revision'] = REVISION
    write(folder/'adapter/adapter_config.json',config)
    templates = Path(__file__).with_name('release')
    for name in ('restore.py','README.md','RELEASE_NOTES.md','requirements.txt','THIRD_PARTY.md'):
        shutil.copyfile(templates/name,folder/name)
    shutil.copyfile(Path(__file__).with_name('requirements-smoke.lock.txt'),folder/'environment.lock.txt')
    # Only runtime types and functions; dataset authoring metadata isn't a runtime dependency.
    contract = Path(__file__).with_name('contract.py').read_text(encoding='utf-8').split('\nclass Example(Strict):')[0]
    (folder/'router_contract.py').write_text(contract.rstrip()+'\n',encoding='utf-8')
    examples = read_examples(ROOT/'validation.jsonl')
    reference = read(RUN/'selected-reloaded-validation.json')
    by_id = {r['id']:r['raw'] for r in reference['rows']}
    with (folder/'recovery_cases.jsonl').open('w',encoding='utf-8',newline='\n') as out:
        for e in examples:
            out.write(json.dumps({'id':e.id,'state':e.state.model_dump(),'reference_raw':by_id[e.id]},ensure_ascii=False)+'\n')
    write(folder/'example_state.json',examples[0].state.model_dump())
    training = {k:v for k,v in registration.items() if k not in ('train_path','base_files','versions')}
    write(folder/'manifest.json',{
        'release':NAME,'checkpoint':'formal-20261028-03/epoch-3','selected_epoch':3,
        'base_model':{'id':MODEL_ID,'revision':REVISION},'base_files':registration['base_files'],
        'adapter_weights_sha256':file_sha256(source/'adapter_model.safetensors'),
        'config_changes':['base_model_name_or_path set to official repository ID','revision pinned'],
        'prompt_sha256':registration['prompt_sha256'],'training':training,
        'runtime':{'python':'3.12.3','platform':'WSL2 Ubuntu / NVIDIA RTX 4060 Laptop 8GB',
                   'versions':registration['versions'],'quantization':'NF4, double quantization, BF16 compute',
                   'non_quantized_parameters':'prepare_model_for_kbit_training casts to FP32',
                   'decoding':reference['decoding']},
        'recovery_cases':36,'recovery_scope':'Parameter and output restoration; no optimizer state or base weights included.'})
    write(folder/'evaluation.json',{
        'models':['base','previous','final'],
        'development_validation':{'count':36,'correct':[29,30,31],'used_for_selection':True},
        'frozen_diagnostic':{'count':36,'correct':[22,28,29],'used_for_selection':False},
        'simulated_research_tasks':{'count':20,'completed':[14,17,19],'simulated_tools':True},
        'data_review':'Synthetic, self-reviewed; independent human review pending',
        'production_integration':False,
        'report':'https://github.com/mutesniper/PaperScout/blob/main/docs/evaluation/router/PROGRESSION_RUN.md',
        'limitations':['Search and budget/resource boundary errors remain','Simulation does not evaluate real answer quality']})
    for path in folder.rglob('*'):
        if not path.is_file() or path.suffix=='.safetensors':
            continue
        text=path.read_text(encoding='utf-8')
        if any(marker in text for marker in ('/mnt/d/', 'C:\\Users\\', 'D:\\github\\', '14693', '[More Information Needed]')):
            raise ValueError(f'Private path or unfinished template in {path.name}')
    checksums = {p.relative_to(folder).as_posix():file_sha256(p) for p in sorted(folder.rglob('*')) if p.is_file()}
    write(folder/'checksums.json',checksums)
    archive = RELEASES/f'{NAME}.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as out:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                out.write(path,arcname=f'{NAME}/{path.relative_to(folder).as_posix()}')
    archive.with_suffix('.zip.sha256').write_text(f'{file_sha256(archive)}  {archive.name}\n',encoding='ascii')
    with zipfile.ZipFile(archive) as packaged:
        assert packaged.testzip() is None
        weight=packaged.read(f'{NAME}/adapter/adapter_model.safetensors')
        assert hashlib.sha256(weight).hexdigest()==file_sha256(source/'adapter_model.safetensors')
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,
                      'files':len(checksums)+1,'weights_unchanged':True},ensure_ascii=False))


if __name__ == '__main__':
    main()
