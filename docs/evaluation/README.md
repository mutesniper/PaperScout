# 评测导航

[项目首页](../../README.md) · [文档中心](../README.md)

## 应用与真实服务

| 文档 | 内容 |
| --- | --- |
| [评测方法](EVALUATION.md) | 固定样本、真实服务的运行入口与计分方式 |
| [论文问答结果](EVALUATION_RESULTS.md) | 实际论文、证据核对与模型用量 |
| [Agent 真实验收与对照](AGENT_EVALUATION_RESULTS.md) | 云端单 / 多 Agent 对照、本地微调路由验收、缓存条件与失败记录 |

## 离线路由实验

| 文档 | 内容 |
| --- | --- |
| [原始模型运行指南](router/BASELINE.md) | 模型文件与隔离环境 |
| [原始基线结果](router/BASELINE_RESULTS.md) | 训练诊断、36条开发验证与解码核验 |
| [数据审核](router/DATA_REVIEW.md) | 场景分组、近邻检查与局限 |
| [QLoRA短试跑](router/QLORA_SMOKE.md) | 显存、梯度、保存与重载 |
| [第一组正式训练](router/FORMAL_RUN.md) | 验证退化结果，不隐藏失败 |
| [第二组与判断边界](router/BOUNDARY_REVIEW.md) | 成对数据补充、30/36开发验证 |
| [第三组与任务进度](router/PROGRESSION_RUN.md) | 顺序与约束样例、冻结诊断集及三模型对照 |
| [第四组保守续训](router/CONSERVATIVE_RUN.md) | 验证提升但多步任务退化；候选未采用，保留第三组 |
| [分层任务评测](router/LAYERED_EVAL.md) | 真实路由模型 + 模拟工具，任务完成与成本 |

应用评测代码位于 `backend/evaluation/`，路由实验代码位于 `training/router/`。原始报告、权重与轨迹在忽略目录 `outputs/`，这里保存可提交的条件、结果和局限。v1历史说明保存在 [DATA_REVIEW_V1](router/DATA_REVIEW_V1.md)，不能作为当前成绩。
