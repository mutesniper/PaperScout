# 后端源码导航

[项目首页](../README.md) · [详细架构](../docs/architecture/README.md)

`app/` 按职责分包：`agent`编排、`llm`模型适配、`search`论文来源、`papers`全文处理、`rag`证据检索与回答、`tools`工具契约和执行。`main.py`负责应用组装，公共结构与存储保留在顶层。

## 入口

从项目根目录启动：

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
.\.venv\Scripts\python -m pytest backend/tests -q
```

`mcp_server.py`、`mcp_smoke.py`以及`evaluation/`入口保持不变。`data/`是运行数据，不能随源码重构搬移或提交；环境变量继续从根目录`.env`读取。

## 旧模块对应关系

以下路径相对于`backend/app/`。自定义Python脚本如直接导入内部模块，需要按新路径更新；HTTP接口及启动命令不变。

| 旧文件 | 新文件 |
| --- | --- |
| `agent.py` | `agent/core.py` |
| `multi_agent.py` | `agent/multi.py` |
| `agent_budget.py` | `agent/budget.py` |
| `models.py` | `llm/client.py` |
| `providers.py` | `search/providers.py` |
| `ranking.py` | `search/ranking.py` |
| `reading.py` | `papers/service.py` |
| `reading_schemas.py` | `papers/schemas.py` |
| `pdf_worker.py` | `papers/pdf_worker.py` |
| `retrieval.py` | `rag/retrieval.py` |
| `qa.py` | `rag/qa.py` |
| `comparison.py` | `rag/comparison.py` |

例如`from app.models import CloudModel`改为`from app.llm.client import CloudModel`。仓库内测试、MCP与评测脚本均已同步；没有留下复制实现或旧路径转发层。
