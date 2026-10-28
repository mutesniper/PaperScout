# PaperScout Router — 最终实验适配器

六类论文研究工具路由的 QLoRA 适配器。输入任务目标与当前状态，输出单字段 JSON：`search`、`download_pdf`、`answer`、`compare`、`finish` 或 `clarify`。

本包为第三组训练的第3轮检查点。只含微调增量参数，不含完整基座；不需要 PaperScout 源码即可恢复推理，不需要重新训练。恢复需要另行下载原始基座，约8GB。

## 模型与环境

- 基座：`Qwen/Qwen3-4B-Instruct-2507`
- 固定 revision：`cdbee75f17c01a7cc42f958dc650907174af0554`
- LoRA：rank 8、alpha 16、dropout 0.05，q/k/v/o 投影。
- 推理：NF4双重量化、BF16计算、贪心解码、最多32个输出Token。
- 验证环境：Windows下的WSL2 Ubuntu、Python 3.12.3、RTX 4060 Laptop 8GB，PyTorch 2.6.0+cu124。
- `requirements.txt`提供主要依赖；`environment.lock.txt`记录完整原环境。其他平台、硬件或依赖组合未验证。

## 恢复步骤

解压后进入包含本文件与`restore.py`的文件夹。以下命令在WSL2/Linux终端执行；已有原训练环境时可直接使用它，不必重复安装。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# 校验发布包，不需要显卡或完整基座
python restore.py --verify-only

# 下载固定版本基座并核对全部文件哈希
python restore.py --base-model ./base-model --download --verify-only

# 用36条参考输入核对恢复后的原始输出
python restore.py --base-model ./base-model --self-test

# 使用随包示例，或以 --input 指定同结构的任务状态JSON
python restore.py --base-model ./base-model --input example_state.json
```

已有完整基座时，将`--base-model`指向原模型文件夹，跳过下载步骤。脚本核对基座文件哈希后离线加载，不会自动改用其他模型或云端服务。

恢复校验预期为`matched: 36`。它验证是否复现已保存的输出，不表示36题全答对；参考输出保留原模型的错误。硬件、驱动和依赖差异可能影响生成结果，文件哈希相同也不保证跨环境逐字一致。

## 对照结果

| 评测 | 原始基座 | 上一版 | 最终版 |
| --- | --- | --- | --- |
| 原开发验证集 | 29/36 | 30/36 | 31/36 |
| 新冻结诊断集 | 22/36 | 28/36 | 29/36 |
| 模拟研究任务完成 | 14/20 | 17/20 | 19/20 |

开发验证集参与选模；新诊断集训练前冻结、不参与选模，但仍是自审的合成数据。多步任务使用模拟工具与固定参数绑定器，不代表真实论文分析质量。模型仍有搜索和预算边界错误，未接入PaperScout正式业务。完整来源与指标见`evaluation.json`和项目实验报告。

## 包内文件

| 文件 | 用途 |
| --- | --- |
| `adapter/adapter_model.safetensors` | 最终微调参数，保持原始字节 |
| `adapter/adapter_config.json` | 移除本机路径，指定官方基座及固定revision |
| `manifest.json` | 基座哈希、训练配置、提示哈希和检查点标识 |
| `router_contract.py` | 推理所需状态结构、原提示词与允许操作检查 |
| `restore.py` | 下载、校验、加载及恢复一致性检查 |
| `recovery_cases.jsonl` | 36条合成参考输入与原始输出，仅用于恢复检查 |
| `example_state.json` | 自定义输入格式示例 |
| `requirements.txt` / `environment.lock.txt` | 依赖与原环境版本 |
| `checksums.json` | 包内文件SHA256清单 |
| `evaluation.json` | 三模型对照与评测范围 |
| `RELEASE_NOTES.md` | 可直接用于GitHub Release的说明 |
| `THIRD_PARTY.md` | 基座来源及许可说明 |

只备份适配器还不够：请同时保留本包及其外部SHA256文件，并确认固定revision的基座仍可获取。长期离线存档还应自行备份基座；本包不包含基座、优化器状态或训练数据，恢复的是推理参数，不是中断位置的训练状态。
