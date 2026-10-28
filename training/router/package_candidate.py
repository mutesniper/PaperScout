"""将第四组候选打包为独立实验发布，不修改默认模型或旧发布包。"""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from .contract import SYSTEM_PROMPT
from .dataset import ROOT, read_examples
from .local_baseline import MODEL_ID, REVISION, file_sha256

REPO = Path(__file__).resolve().parents[2]
RUN = REPO/'outputs/router/conservative-20261028-01'
EVALUATION = REPO/'outputs/router/conservative-eval-20261028-01'
NAME = 'paperscout-router-v1.1.0-experimental.1'
TAG = 'router-v1.1.0-experimental.1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    result=read(RUN/'result.json')
    registration=read(RUN/'preregistered.json')
    summary=read(EVALUATION/'summary.json')
    decision=read(EVALUATION/'decision.json')
    reference=read(RUN/'reloaded-validation.json')
    if result['status']!='complete' or result['selected_epoch']!=2 or summary['status']!='complete':
        raise ValueError('必须是已完成评测的第二轮候选')
    if not all(read(RUN/'reload_check.json').values()):
        raise ValueError('独立重载未通过')
    if decision['decision']!='retain_incumbent' or decision['eligible'] or decision['applied']:
        raise ValueError('发布说明仅适用于未采用的本次实验候选')
    prompt_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
    if prompt_hash!=registration['prompt_sha256']:
        raise ValueError('提示词发生变化')
    source=RUN/'epoch-2/adapter'
    weights_hash=file_sha256(source/'adapter_model.safetensors')
    if weights_hash!=read(EVALUATION/'metadata.json')['candidate_weights_sha256']:
        raise ValueError('候选权重与已评测版本不同')
    selected=read(RUN/'epoch-2/validation.json')
    if [r['raw'] for r in selected['rows']]!=[r['raw'] for r in reference['rows']]:
        raise ValueError('保存后输出与重载输出不一致')
    folder=REPO/'outputs/router/releases'/NAME
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'adapter').mkdir()
    shutil.copyfile(source/'adapter_model.safetensors',folder/'adapter/adapter_model.safetensors')
    config=read(source/'adapter_config.json')
    config.update(base_model_name_or_path=MODEL_ID,revision=REVISION)
    write(folder/'adapter/adapter_config.json',config)
    templates=Path(__file__).with_name('release')
    for name in ('restore.py','requirements.txt','THIRD_PARTY.md'):
        shutil.copyfile(templates/name,folder/name)
    shutil.copyfile(Path(__file__).with_name('requirements-smoke.lock.txt'),folder/'environment.lock.txt')
    report=(REPO/'docs/evaluation/router/CONSERVATIVE_RUN.md').read_text(encoding='utf-8')
    report=report.replace('/home/mutesniper/.venvs/paperscout-router/bin/python','python')
    (folder/'EXPERIMENT_REPORT.md').write_text(report,encoding='utf-8')
    contract=Path(__file__).with_name('contract.py').read_text(encoding='utf-8').split('\nclass Example(Strict):')[0]
    (folder/'router_contract.py').write_text(contract.rstrip()+'\n',encoding='utf-8')
    examples=read_examples(ROOT/'validation.jsonl')
    outputs={r['id']:r['raw'] for r in reference['rows']}
    with (folder/'recovery_cases.jsonl').open('w',encoding='utf-8',newline='\n') as stream:
        for example in examples:
            stream.write(json.dumps({'id':example.id,'state':example.state.model_dump(),
                                    'reference_raw':outputs[example.id]},ensure_ascii=False)+'\n')
    write(folder/'example_state.json',examples[0].state.model_dump())
    keys=('epochs','seed','peak_learning_rate','end_learning_rate','warmup_updates',
          'gradient_accumulation','batch_size','max_length','optimizer','weight_decay',
          'max_grad_norm','updates','selection','train_count','train_sha256','prompt_sha256')
    training={k:registration[k] for k in keys}
    training['source_file_hashes']={Path(k).name:v for k,v in registration['source_files'].items()}
    write(folder/'manifest.json',{'release':NAME,'tag':TAG,'checkpoint':'conservative-20261028-01/epoch-2',
        'selected_epoch':2,'experimental':True,'application_default':False,
        'adoption_decision':'retain_incumbent','parent_adapter_release':'router-v1.0.0',
        'base_model':{'id':MODEL_ID,'revision':REVISION},'base_files':registration['base_files'],
        'adapter_weights_sha256':weights_hash,'prompt_sha256':prompt_hash,'training':training,
        'config_changes':['使用官方基座标识替换本机路径','固定基座 revision；权重字节不变'],
        'runtime':{'python':'3.12.3','platform':'WSL2 Ubuntu / RTX 4060 Laptop 8GB',
            'versions':registration['versions'],'quantization':'NF4 double quantization, BF16 compute',
            'non_quantized_parameters':'prepare_model_for_kbit_training casts to FP32','decoding':reference['decoding']},
        'recovery_cases':len(examples),'recovery_scope':'完整适配器参数与推理输出恢复，不含基座或优化器状态。'})
    write(folder/'evaluation.json',summary)
    write(folder/'acceptance.json',decision)
    notes='''# PaperScout Router v1.1.0-experimental.1

第四组保守续训的第 2 轮候选，作为独立实验附件发布，**不替换应用默认模型 router-v1.0.0**。

基于 Qwen3-4B-Instruct-2507，在当前 LoRA 适配器上使用 300 条样例续训两轮（150 次更新）。训练采用 NF4/BF16、rank 8，学习率从 8e-6 逐渐降到 2e-6。

| 指标 | 原始基座 | 当前默认 v1.0.0 | 本实验候选 |
| --- | --- | --- | --- |
| 开发验证 | 29/36 | 31/36 | 33/36 |
| 旧诊断集 | 22/36 | 29/36 | 30/36 |
| 测试候选 | 101/120 | 105/120 | 105/120 |
| 模拟研究任务 | 14/20 | 19/20 | 18/20 |

单步验证有所提升，但新增了禁止联网时仍下载、未澄清选择就直接对比的错误。因此未通过不退化验收，保留为实验版本。评测数据为合成自审样例；多步任务使用模拟工具与固定参数绑定，不代表真实论文分析质量。

## 下载与恢复

- 下载 `paperscout-router-v1.1.0-experimental.1.zip` 和同名 `.zip.sha256`，不要将 GitHub 自动生成的 Source code 压缩包当作模型附件。
- 包内包含完整 LoRA 增量参数、固定基座版本、依赖、独立恢复脚本、36 条输出一致性样例及完整实验报告。
- 基座权重需另行准备；恢复推理只需基座与本包，不需要再加载 v1.0.0 适配器，也不需要重新训练。
- 先执行 `python restore.py --verify-only`；准备固定版本基座后执行 `python restore.py --base-model /path/to/base --self-test`。
- 预期 36/36 输出与保存记录一致，**不是 36/36 答题正确**。新环境安装、重新下载基座及其他硬件环境未验证。

当前 PaperScout 本地路由服务固定校验默认版身份，不能直接用本候选替换默认权重文件。请通过本包的独立恢复脚本进行实验。

附件不包含完整基座、密钥、个人论文、运行数据库或优化器状态。第三方来源与许可见 `THIRD_PARTY.md`。
'''
    (folder/'RELEASE_NOTES.md').write_text(notes,encoding='utf-8')
    readme=notes+'''
## 完整命令

在 WSL2/Linux 的 Python 3.12 环境中运行，已有训练环境可直接复用：

```bash
python -m pip install -r requirements.txt
python restore.py --verify-only
# 没有基座时才执行下载；已存在时直接传入原基座目录
python restore.py --base-model ./base-model --download --verify-only
python restore.py --base-model ./base-model --self-test
python restore.py --base-model ./base-model --input example_state.json
```

`environment.lock.txt`记录原环境，`manifest.json`固定模型、提示词与训练配置，`checksums.json`核对包内文件。完整参数字节保留在`adapter/adapter_model.safetensors`；`adapter_config.json`仅替换本机路径并固定基座版本。`acceptance.json`保留拒绝采用的逐题原因。

恢复脚本会检查基座和包内文件哈希；硬件和依赖差异仍可能影响逐字输出。下载固定版本基座需联网，校验通过后的推理只使用本地文件。
'''
    (folder/'README.md').write_text(readme,encoding='utf-8')
    for path in folder.rglob('*'):
        if path.is_file() and path.suffix!='.safetensors':
            text=path.read_text(encoding='utf-8')
            if any(x in text for x in ('/mnt/d/','/home/','C:\\Users\\','D:\\github\\','14693','[More Information Needed]')):
                raise ValueError(f'文件含本机路径或未完成模板：{path.name}')
    checksums={p.relative_to(folder).as_posix():file_sha256(p) for p in sorted(folder.rglob('*')) if p.is_file()}
    write(folder/'checksums.json',checksums)
    archive=folder.parent/(NAME+'.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as target:
        for path in sorted(folder.rglob('*')):
            if path.is_file():target.write(path,f'{NAME}/{path.relative_to(folder).as_posix()}')
    archive.with_suffix('.zip.sha256').write_text(f'{file_sha256(archive)}  {archive.name}\n',encoding='ascii')
    with zipfile.ZipFile(archive) as target:
        if target.testzip() is not None:raise ValueError('压缩包校验失败')
        if hashlib.sha256(target.read(f'{NAME}/adapter/adapter_model.safetensors')).hexdigest()!=weights_hash:
            raise ValueError('打包改变了权重')
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,
                      'sha256':file_sha256(archive),'weights_sha256':weights_hash,'files':len(checksums)+1}))


if __name__=='__main__':
    main()
