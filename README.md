# PaperScout

**从研究想法出发，找到论文，读懂内容，追溯证据。**

一个支持多来源检索、PDF 摘要、RAG 问答和论文对比的本地研究工作台。既可以手动探索，也可以让单 Agent 或多 Agent 根据目标调用工具完成任务。

`Vue 3` · `TypeScript` · `FastAPI` · `LangGraph` · `FAISS` · `SQLite` · `MCP` · `Docker`

[快速启动](#快速启动) · [配置](#配置) · [项目结构](#项目结构) · [文档导航](#文档导航)

## 能做什么

| 功能 | 说明 |
| --- | --- |
| 论文发现 | arXiv / Semantic Scholar 多来源检索；自然语言生成检索词；相关性、日期和引用量排序 |
| PDF 摘要 | 获取公开 PDF 或上传本地文件，生成全文摘要并保留原句依据 |
| 单篇问答 | 关键词或语义检索，回答附原文片段与 PDF 页码 |
| 多篇对比 | 选择 2～5 篇论文，按研究目标、方法、实验、结果和局限对比 |
| 研究任务 | 默认单 Agent；可选协调、检索、分析三角色协作，支持进度、取消与历史 |
| 导出与集成 | 引用生成、Markdown 导出，以及供外部客户端调用的 MCP 工具 |

界面提供模块快捷导航和集中式「使用指引」。详细规则见 [功能说明](docs/USAGE.md)。

## 快速启动

### Docker

先安装并启动 Docker Desktop。在项目根目录运行：

```powershell
# 已有配置不会被覆盖
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 按需填写模型配置，然后启动
docker compose up --build -d
```

打开 [PaperScout](http://localhost:8080)。容器数据保存在独立持久化卷，与本地运行的数据分开。停止、更新及演示步骤见 [启动与演示](docs/DEMO.md)。

### 本地开发 · Windows PowerShell

需要 **Python 3.11+**、**Node.js 20.19+ 或 22.12+**，以及可访问论文来源的网络。

在项目根目录启动后端：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements-lock.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

另开终端，在项目根目录启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

访问 [前端页面](http://127.0.0.1:5173) · [API 文档](http://127.0.0.1:8000/docs)。

## 配置

在根目录 `.env` 填写；只由后端读取，修改后需重启后端。完整变量说明见 [配置文档](docs/CONFIGURATION.md)。

| 配置组 | 用途 | 是否必需 |
| --- | --- | --- |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 摘要、问答、对比、检索计划与 Agent | 使用模型功能时需要 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` | 语义检索 | 可选；全部留空使用关键词检索 |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar 来源认证 | 可选；公共接口可能限流 |
| `LLM_SUMMARY_MAX_TOKENS` | 摘要单次输出额度 | 可选；默认 4096 |

不配置模型也能手动检索、上传 PDF 和查看已保存数据。Embedding 配置须全部填写或全部留空，不自动复用生成模型配置。

## 项目结构

```text
PaperScout/
├── backend/
│   ├── app/                 # API、来源、模型、RAG 与 Agent 编排
│   │   └── tools/           # Agent 动作定义、搜索策略与统一执行入口
│   ├── evaluation/          # 固定样本、真实论文准备与评测入口
│   ├── tests/               # 后端测试与真实来源冒烟脚本
│   ├── mcp_server.py        # MCP stdio 服务
│   ├── mcp_smoke.py         # MCP 冒烟检查
│   ├── requirements*.txt    # 基础依赖、锁定版本与可选 MCP 依赖
│   └── data/                # 运行时数据库和 PDF（不入库）
├── frontend/
│   ├── src/                 # Vue 页面、组件、样式与导出逻辑
│   ├── tests/               # Playwright 浏览器测试
│   └── package.json         # 前端依赖与构建命令
├── deploy/nginx.conf        # Docker 前端与 API 反向代理
├── docs/                    # 使用、架构、计划与评测文档
├── outputs/                 # 本地产物与评测输出（不入库）
├── .env.example             # 配置模板
├── compose.yaml             # Docker 服务及持久化卷
├── Dockerfile               # 前后端构建
└── pytest.ini               # 后端测试配置
```

模块级职责和数据流见 [目录与架构](docs/ARCHITECTURE.md)。

## 文档导航

| 想了解什么 | 文档 |
| --- | --- |
| 启动、停止和演示 | [启动与演示](docs/DEMO.md) |
| 检索、摘要、问答与 Agent 使用规则 | [功能说明](docs/USAGE.md) |
| 模型和 Embedding 配置 | [配置说明](docs/CONFIGURATION.md) |
| 代码模块与数据存储 | [目录与架构](docs/ARCHITECTURE.md) |
| 测试与依赖管理 | [开发与验证](docs/DEVELOPMENT.md) |
| 接入外部 MCP 客户端 | [MCP 工具接口](docs/MCP.md) |
| 评测方法与真实验证结果 | [评测说明](docs/EVALUATION.md) · [论文评测](docs/EVALUATION_RESULTS.md) · [Agent 对照](docs/AGENT_EVALUATION_RESULTS.md) |
| 后续计划与开发记录 | [项目计划](docs/PLAN.md) · [进度记录](docs/PROGRESS.md) |

## 当前边界

- 面向本地单用户，尚无用户认证和多用户数据隔离；使用单后端进程运行。
- PDF 支持文字型文件，限制 20 MB / 100 页；不支持扫描件 OCR。公开链接不保证下载成功，也不能据缺失链接判断论文付费。
- 模型调用可能产生费用；证据引用可追溯不代表结论必然正确，重要信息仍需核对原文。
- 单／多 Agent 完整流程已有真实验收记录，但单次对照不能证明普遍性能优势。小模型微调进入下一阶段，见 [微调与接入计划](docs/FINETUNING.md)。
