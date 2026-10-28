# 原始小模型基线

状态：**原始模型正式基线已完成：训练集55/84、验证集29/36，测试候选未评分。** 详见[结果与失败记录](BASELINE_RESULTS.md)。

2026-10-28用户完成4B权重下载，文件检查与加载通过。已创建独立WSL环境`/home/mutesniper/.venvs/paperscout-router`，安装推理依赖及WSL编译工具；未向后端环境安装这些依赖。

## 手动下载

使用[官方固定版本文件页](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/tree/cdbee75f17c01a7cc42f958dc650907174af0554)。权重约8.05GB，加上配置和分词文件共约8.06GB；请保存实际文件，不要保存网页或Git LFS指针。

建议目录：`D:\github\Project\PaperScout\outputs\router\models\Qwen3-4B-Instruct-2507`，属于已忽略目录。

下载以下10个文件，保持文件名不变、放在同一目录：

```text
config.json
generation_config.json
tokenizer.json
tokenizer_config.json
merges.txt
vocab.json
model.safetensors.index.json
model-00001-of-00003.safetensors
model-00002-of-00003.safetensors
model-00003-of-00003.safetensors
```

请勿下载不同模型、GGUF或第三方量化权重。这里使用原始checkpoint加载时做NF4量化，以便后续与同基座QLoRA比较。文件完整性预检查不等于已经验证模型可加载；实际运行会记录本地全部文件SHA256，来源版本需与上面下载页一致。

下载后可用现有Windows虚拟环境检查文件，不需要先安装推理依赖：

```powershell
.\.venv\Scripts\python -X utf8 -m training.router.local_baseline --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --preflight-only
```

## 运行方式

已在独立WSL虚拟环境验证PyTorch/CUDA、Transformers、Accelerate、bitsandbytes及Pydantic。实际版本见requirements-inference.lock.txt，直接依赖见requirements-inference.txt；不复用后端环境。首次量化加载需要系统C编译器和Python开发头文件（build-essential、python3-dev）。

从WSL的仓库目录`/mnt/d/github/Project/PaperScout`执行：

```bash
source /home/mutesniper/.venvs/paperscout-router/bin/activate
python -m training.router.local_baseline --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --split validation --tokenize-only
python -m training.router.local_baseline --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --split validation
python -m training.router.local_baseline --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --split train
```

先统计真实chat template后的Token长度，再跑36条验证与84条训练诊断；没有测试候选选项。默认输入上限2048，超长直接报错，不截断。默认生成32Token、贪心解码，不做JSON修复或强制工具选择，方便评测原始输出合法率。

## 结果边界

- 全程local_files_only、禁用远程代码、关闭Hub在线模式，无自动下载和云端回退。
- 需要CUDA，使用NF4双重量化与BF16，不加载adapter。原始模型指未微调checkpoint；结果是量化推理成绩，不是全精度成绩。
- 每轮使用独立目录，不覆盖规则基线。记录模型文件、数据、提示哈希、实际依赖、输入长度、输出、耗时及峰值显存；预热不计入成绩。
- 逐条写入predictions.jsonl，只有全部完成才生成report.json；中断或运行失败写incomplete.json，不将部分成绩包装成完整基线。
- 入口边界检查和本次真实推理均已完成；不得把这些结果当作微调成功或Agent完整流程验收。
