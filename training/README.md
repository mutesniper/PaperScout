# 独立训练与路由评测

[项目首页](../README.md) · [训练计划](../docs/plans/FINETUNING.md) · [评测报告](../docs/evaluation/README.md)

`router/` 是独立离线 Python 模块，正式后端不导入它。保留 `python -m training.router.…` 入口，不需要将训练依赖加入后端环境。

| 位置 | 职责 |
| --- | --- |
| `router/contract.py` | 六类路由与任务状态契约 |
| `router/data/` | 版本化数据与哈希清单 |
| `router/*_data.py` | 训练侧定向补充与判断边界样例 |
| `router/local_baseline.py` | 原始模型基线 |
| `router/qlora_smoke.py` / `formal_train.py` | 短试跑与正式QLoRA |
| `router/task_suite.py` / `layered_eval.py` | 受控多步任务与分层评测 |
| `router/serve.py` / `runtime_profile.json` | 最终适配器本地HTTP服务与固定版本校验 |
| `router/progression_data.py` / `progression_eval.py` | 任务进度样例、冻结诊断集与三模型对照 |
| `router/conservative_data.py` / `conservative_run.py` / `conservative_eval.py` | 保守续训、旧数据回放与不退化验收 |
| `router/test_*.py` | 不加载大模型的离线测试 |
| `router/requirements*.txt` | 独立WSL依赖与版本快照 |
| `../docs/evaluation/router/` | 实验说明与报告 |
| `../outputs/router/` | 忽略的模型、适配器、日志及原始结果 |

第三组适配器作为当前单 Agent 实验路由使用，见[启动说明](../docs/guides/LOCAL_ROUTER.md)。[第四组保守续训](../docs/evaluation/router/CONSERVATIVE_RUN.md)已完成：开发验证提升，但网络权限判断及模拟任务退化，因此候选未采用。训练与评测入口见[router/README](router/README.md)；模拟任务完成率不等于真实论文工作流质量。
