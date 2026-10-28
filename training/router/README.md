# 工具路由：离线契约与 v2 场景数据

[总计划](../../docs/plans/FINETUNING.md)

训练与评测仍独立于正式后端；后端不导入本目录。另提供`serve.py`将最终适配器加载为本地HTTP服务，单Agent可选择该实验路由，启动与限制见[使用说明](../../docs/guides/LOCAL_ROUTER.md)。

原始4B模型验证29/36；第一组最佳27/36，第二组30/36（168条训练数据），第三组31/36（228条训练数据）。第三组增加进度与约束样例，独立重载通过，但出现预算边界回归，仍未达接入标准。后续第四组已首次评分120条测试候选，原始/当前/候选分别101/120、105/120、105/120；候选因新增边界错误及模拟任务退化未采用，见[第四组报告](../../docs/evaluation/router/CONSERVATIVE_RUN.md)。见[第三组对比](../../docs/evaluation/router/PROGRESSION_RUN.md)、[第二组](../../docs/evaluation/router/BOUNDARY_REVIEW.md)、[第一组](../../docs/evaluation/router/FORMAL_RUN.md)、[基线结果](../../docs/evaluation/router/BASELINE_RESULTS.md)与[运行说明](../../docs/evaluation/router/BASELINE.md)。

v2取消前缀/ID扩写，现有训练84条、验证36条、测试候选120条不同的目标—状态案例。旧候选全部转为训练侧，新评测按完整任务族隔离；仍为作者自审的合成数据，不是独立人工金标准。详见 [数据审核与基线记录](../../docs/evaluation/router/DATA_REVIEW.md)。

## 已提供

- `contract.py`：六类严格路由输出、紧凑输入、按资源与预算计算允许操作、模型提示与原始输出校验。
- `build_specification.py`：60 条由 Codex 编写与自审的合成场景定义。
- `specification.jsonl`：可逐条检查的规格样例，每类 10 条；包含来源、场景组、主标签、可接受标签和理由。
- `validate.py`：检查格式、重复、类别分布、动作前置条件；不判断自然语言标注是否正确。
- `test_contract.py`：校验输出污染、预算、重复下载、ID 命名空间和标注泄漏等边界。
- `diverse_scenarios.py`：156条新增状态，明确标注6个验证与12个测试候选任务族。
- `dataset.py`：无扩写导出、旧候选退役、分组/重复/覆盖校验与哈希清单。
- `similarity.py`、`similarity_review.json`：跨集合近邻报告和逐对作者审核；不保证所有语义近邻都能自动发现。
- `targeted_data.py`、`data/targeted/`：训练侧反事实状态补充，不修改v2验证/测试候选。
- `boundary_data.py`、`data/boundary/`：36条判断边界成对补充，原132条标签不变，第二组共168条。
- `progression_data.py`、`data/progression/`：第三组补充60条任务进度/约束样例，共228条；另冻结36条训练外诊断，原验证/测试候选不变。
- `progression_eval.py`：选模后对原始模型、第二组最佳、第三组最佳运行同一冻结诊断集，不用其结果选择检查点。
- `conservative_data.py` / `conservative_run.py` / `conservative_eval.py`：第四组旧数据回放与低学习率续训。选模后对照当前版，首次评分120条测试候选，逐题退化即拒绝替换；见[实验记录](../../docs/evaluation/router/CONSERVATIVE_RUN.md)。
- `qlora_smoke.py`：assistant-only监督检查、最多20次参数更新、保存与独立进程重载核验；不用于正式效果验收。
- `formal_train.py`、`compare_formal.py`：固定3轮正式训练、同验证集选模、独立重载及原始模型对比，不开放测试评分或自动部署。
- `task_suite.py`、`layered_eval.py`：24个受控多步任务的分层评测，真实本地路由+模拟工具；首次14/20→17/20，第三组回归19/20，尚非真实论文工作流验收。见[分层评测](../../docs/evaluation/router/LAYERED_EVAL.md)及[第三组报告](../../docs/evaluation/router/PROGRESSION_RUN.md)。

六类是 `search`、`download_pdf`、`answer`、`compare`、`finish`、`clarify`。
`clarify` 在本地路由模式中对应暂停并等待用户补充，随后可恢复任务。`finish` 包含成功完成和明确受阻；运行时不能把两者都报告为成功。

## 如何检查

从项目根目录使用现有后端虚拟环境，无需安装训练依赖：

```powershell
.\.venv\Scripts\python -m training.router.validate
.\.venv\Scripts\python -m pytest training/router/test_contract.py -q
```

重建数据草案、运行验证集规则基线、检查全部离线测试：

```powershell
.\.venv\Scripts\python -m training.router.dataset
.\.venv\Scripts\python -m training.router.similarity
.\.venv\Scripts\python -m training.router.evaluate
.\.venv\Scripts\python -m pytest training/router -q
```

`data/manifest.json`记录集合数量、覆盖、审核状态和哈希。`train.messages.jsonl`只含模型消息，不把标注理由放入模型输入。`evaluate`目前仅允许train/validation，不提供测试候选评分入口；v2报告写入独立文件，不覆盖v1报告。评测保留所有失败在分母中，分别计算JSON合法、动作可用、标签命中、场景族均值和混淆矩阵。

近邻阈值0.30标出的28对已经结合完整状态自审，审核记录绑定双方输入哈希。修改数据后需重新检查近邻、必要时调整完整任务族归属，再修改审核理由；不能把重新生成审核文件当作审核本身。同族分支相关，240行不等于240个统计独立任务。

修改场景定义后重新生成：

```powershell
.\.venv\Scripts\python -m training.router.build_specification
```

审核时按 `scenario_group` 连续查看同任务的不同状态，检查 `state`、`expected`、`rationale` 是否一致。
用户只输入论文名称但没有任务、存在多个可能指代、明确要求之间冲突，都应澄清；单纯缺少年份或篇数可用合理默认值，不应澄清。
已有足够相关候选优先下载；已有所需 PDF 优先分析；只需链接且已提供链接就结束。

这些是**开发规格，不是独立测试集，也不是已验证的训练成果**。所有标签目前只有 Codex 自审，独立审核为 pending。允许标签数组支持未来加入合理等价动作，目前样例按上述优先规则指定一个主动作。
提示内容不包含标签、标注理由、标签派生的任务类型或“目标已完成”布尔值；完成情况由已交付结果描述体现。
允许列表只取决于资源、预算与取消状态，通常包含多个动作，不能将它缩成正确答案。

## 已知边界与下一步

- 输入的 `recent_outcomes` 在本阶段是完整的相关操作记录；后续接入不能截掉已尝试下载信息，须从完整运行记录构造它或增加专用去重字段。
- 本阶段没有自动把真实运行记录转换成输入；`State` 用于合成状态与离线校验。
- 标题和工具输出中的恶意指令应由路由模型忽略；这里只提供测试样例，不表示模型已经通过抗注入验证。
- 指定模型的真实tokenizer已检查补充后132条数据，最长433 Token，无截断；监督仅覆盖JSON和EOS。
- 用户授权补充数据后完成第三组正式配置；验证31/36仍未通过验收。不自动追加第四组，不反复刷验证集当泛化成绩。测试候选未评分，独立审核待完成。
- 独立WSL环境已安装PEFT，短试跑直接使用PyTorch+PEFT，没有安装TRL，没有执行云端扩写或付费评测。
