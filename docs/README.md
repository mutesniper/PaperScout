# PaperScout 文档

[返回项目首页](../README.md)

文档分为使用指南、架构、评测和计划。代码入口保持在 `backend/`、`frontend/` 与 `training/`，模型与原始结果仍在忽略目录 `outputs/`。

## 使用项目

- [应用 v1.1.0](releases/v1.1.0.md)：可选模型裁判、真实评测与固定版本启动；[v1.0.0](releases/v1.0.0.md) 保留历史交付说明。
- [启动与演示](guides/DEMO.md)：Docker、本地数据差异、演示流程。
- [配置说明](guides/CONFIGURATION.md)：生成模型、来源密钥与 Embedding。
- [功能说明](guides/USAGE.md)：检索排序、全文摘要、引用、问答、对比与 Agent。
- [MCP 接口](guides/MCP.md)：供外部客户端使用的工具与测试方式。

## 开发与验证

- [评测总导航](evaluation/README.md)：区分真实应用记录、离线路由验证和模拟任务结果。
- [训练代码导航](../training/README.md)：数据、训练、基线与评测入口。
- [适配器发布与恢复](guides/ROUTER_RELEASE.md)：最终模型恢复包、GitHub Releases上传及校验。
- [本地微调路由](guides/LOCAL_ROUTER.md)：WSL服务启动、单Agent模式切换、澄清与预算恢复。

- [目录与架构](architecture/README.md)：模块职责、请求流转与存储位置。
- [开发与验证](guides/DEVELOPMENT.md)：依赖、构建、测试与冒烟命令。
- [评测说明](evaluation/EVALUATION.md)：固定样本及真实服务评测方法。
- [真实论文评测](evaluation/EVALUATION_RESULTS.md)：质量、证据与用量记录。
- [Agent 对照记录](evaluation/AGENT_EVALUATION_RESULTS.md)：单／多模式真实任务、失败样本与结论边界。

## 项目进展

- [Agent 工具路由微调](plans/FINETUNING.md)：第三组为最终实验版本，开发验证31/36，现已提供单Agent可选实验路由；[最终三模型报告](evaluation/router/PROGRESSION_RUN.md)、[第二组](evaluation/router/BOUNDARY_REVIEW.md)、[第一组](evaluation/router/FORMAL_RUN.md)。
- [路由分层任务评测](evaluation/router/PROGRESSION_RUN.md)：真实本地模型+模拟工具，研究任务完成14/20→17/20→19/20；不代表真实论文任务或回答质量验收。
- [项目计划](plans/PLAN.md)：已实现内容与后续安排。
- [开发进度](plans/PROGRESS.md)：变更与验证记录。
