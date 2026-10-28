# QLoRA 短试跑记录

2026-10-28。仅验证本机训练流程，不是正式训练成绩，也没有接入应用。

## 数据与监督

- 针对原始模型训练侧错误新增48条样例，合并后132条，存放在 `data/targeted/`；原v2文件保留。
- 覆盖相同目标在不同资源/进度下的动作、取消与预算、指代歧义、明确对象、子目标顺序及英文请求。
- 六类数量：search 21、download_pdf 22、answer 21、compare 18、finish 26、clarify 24。不是随机重复扩写。
- 验证36条、测试候选120条的文件哈希不变；本次没有对它们调用模型。新增数据的跨集合文本近邻检查在0.30阈值下为0对，记录在 `data/targeted/similarity_audit.json`。这不证明语义独立，独立审核仍待完成。
- 真实tokenizer检查全部132条：298–433 Token，无截断；仅assistant的路由JSON及EOS参与损失，每条6–7个监督Token，system/user全部标为-100。
- 实际短试跑取48个不同训练状态，每类8条，包含最长输入。相同目标的不同状态不是统计独立任务。

## 配置与结果

基座沿用固定版本Qwen3-4B-Instruct-2507，原始权重不修改。独立WSL环境使用PyTorch 2.6.0+cu124、Transformers 4.51.3、PEFT 0.15.2、bitsandbytes 0.45.5。完整环境见 `requirements-smoke.lock.txt`；本次新增PEFT，没有安装TRL，也没有修改后端虚拟环境。

| 项目 | 实测/配置 |
| --- | --- |
| 量化与计算 | NF4、双重量化、BF16 autocast、PEFT kbit准备 |
| LoRA | rank 8、alpha 16、dropout 0.05，q/k/v/o投影 |
| 可训练参数 | 5,898,240，仅LoRA参数 |
| 更新 | 12次，batch 1，梯度累积4 |
| 优化器 | AdamW，学习率2e-5，weight decay 0，梯度裁剪1 |
| 长度上限 | 512，禁止静默截断，启用梯度检查点 |
| 训练循环耗时 | 41.95秒，不含哈希检查、权重加载、保存与重载 |
| PyTorch峰值分配显存 | 4.93 GiB |
| PyTorch峰值预留显存 | 5.34 GiB，区别于整机GPU占用 |
| 首/末更新平均loss | 1.9869 / 0.2641，来自不同批次，不能据此声称泛化改善 |
| 梯度 | 12次均为有限非零值，无OOM |
| 最大适配器权重变化 | 0.0002397014，确认执行了参数更新 |
| 适配器目录大小 | 约22.54 MiB |
| 云端调用 | 0 |

独立进程重载已通过：基座文件哈希未变，全部适配器张量哈希一致，两条训练探针的贪心输出Token逐个一致。该检查只证明保存/重载可复现，不证明路由正确；其中 `targeted-017` 仍把应澄清的请求选为 `compare`，不能把这次短试跑当作能力达标。

原始记录位于 `outputs/router/qlora-smoke-20261028-01/`：`run_config.json`、`steps.jsonl`、`training_report.json`、`reload_report.json`、`adapter/`。模型权重与原始输出已被Git忽略。42项离线检查和独立训练环境的 `pip check` 通过；未修改业务路径，未重跑应用端到端测试。

## 复现

在项目根目录重建训练补充数据、检查单元测试：

```powershell
.\.venv\Scripts\python -m training.router.targeted_data
.\.venv\Scripts\python -m pytest training/router -q
```

以下命令在WSL中执行，使用独立环境。先检查监督，再训练，再用另一个进程重载。同一次训练/重载须使用相同输出目录；新训练必须换目录，脚本拒绝覆盖旧实验。

```bash
cd /mnt/d/github/Project/PaperScout
PY=/home/mutesniper/.venvs/paperscout-router/bin/python
$PY -m training.router.qlora_smoke --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --mode inspect
$PY -m training.router.qlora_smoke --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --mode train --output-dir outputs/router/qlora-smoke-new
$PY -m training.router.qlora_smoke --model-path outputs/router/models/Qwen3-4B-Instruct-2507 --mode reload --output-dir outputs/router/qlora-smoke-new
```

## 下一步

补完数据审核、固定正式训练与验证规则后，从原始基座重新开始正式训练，不续接这次短试跑适配器。最多两组预先记录的配置，按既定验证集指标比较原始模型；不通过则停止接入。测试候选保持未评分。此次没有得到正式微调准确率或Agent闭环成绩。
