# 本地 MCP 接入

PaperScout 提供官方 Python MCP SDK（固定 `mcp==1.30.0`，v1 维护线）的 stdio 服务。MCP 客户端启动一个本地适配进程，适配进程调用正在运行的 PaperScout HTTP 后端。网页和 MCP 使用同一个后端的数据、模型配置、检索和证据校验逻辑；没有启动第二个数据库或另一套 Agent。

## 启动与连接

1. 在项目根目录安装依赖：

```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-lock.txt -r backend\requirements-mcp.txt
```

2. 照常启动本地后端（8000），或 Docker Compose（8080）。MCP 不需要启动前端开发服务器。模型密钥仅配置在后端的 `.env`，不放进客户端 MCP 配置。

3. 在支持 stdio 的 MCP 客户端添加服务器。通用 `mcpServers` 配置如下；具体客户端的配置入口可能不同。替换为自己的绝对路径：

```json
{
  "mcpServers": {
    "paperscout": {
      "command": "D:/github/Project/PaperScout/.venv/Scripts/python.exe",
      "args": [
        "-X", "utf8",
        "D:/github/Project/PaperScout/backend/mcp_server.py",
        "--base-url", "http://127.0.0.1:8000"
      ]
    }
  }
}
```

Docker 后端将最后一个地址改为 `http://127.0.0.1:8080`。MCP 进程仍在宿主机运行，不需要重建 Docker 镜像。连接 8000 和 8080 可能是不同数据库，请选择实际使用的那一个。当前仅接受本机 HTTP 地址，不提供远程 HTTP MCP 端点。

客户端会启动进程，不必另开终端手动运行 `mcp_server.py`。手动启动后等待输入是正常的 stdio 行为，不是网页服务。

## 工具

| 工具 | 输入 | 行为 |
| --- | --- | --- |
| `list_papers` | 无 | 读取论文库列表，不调用模型 |
| `get_paper` | `reading_id` | 读取元数据与已有摘要，不返回全文或生成摘要 |
| `search_papers` | `query` | 查询来源并保存检索历史，不调用模型 |
| `import_paper_pdf` | `record_id`, `paper_id` | 下载已保存检索结果中的公开 PDF，解析后入库 |
| `summarize_paper` | `reading_id` | 调用后端模型生成摘要并保存；长文可能多次调用 |
| `ask_paper` | `reading_id`, `question` | RAG 问答并保存；可产生生成模型、Embedding 费用 |
| `compare_papers` | `comparison` | 2～5 篇 PDF 对比并保存；可产生模型费用 |

按工具结构化输入依次调用：

```json
{"query":{"plan":{"queries":["small language models tool use"]},"sources":["arxiv"],"limit":5,"sort":"relevance"}}
```

从搜索结果取得顶层 `id`（检索记录 ID）和 `papers` 中某篇的 `id`：

```json
{"record_id":"搜索返回的记录ID","paper_id":"选中论文的ID"}
```

从导入结果取得 `id`（论文库 ID），再提问：

```json
{"reading_id":"导入返回的ID","question":{"question":"这篇论文采用了什么方法？请给出原文依据。"}}
```

对比输入：

```json
{"comparison":{"reading_ids":["论文库ID1","论文库ID2"],"dimensions":["goal","method","limitations"]}}
```

客户端可直接输入：“搜索 small language models tool use，先用 arXiv，导入一篇有公开 PDF 的论文，回答其方法并保留原文依据。”这条链路涉及网络下载及模型费用；本工具不会自动逐篇生成摘要。已有 PDF 可以在网页上传后通过 `list_papers` 取得 ID。

## 测试

只读连通测试（不调用模型、不新增历史）：

```powershell
.\.venv\Scripts\python backend\mcp_smoke.py
# Docker 后端
.\.venv\Scripts\python backend\mcp_smoke.py --base-url http://127.0.0.1:8080
```

预期列出 7 个工具，报告后端连通；论文库非空时再读取一篇元数据。

协议与适配自动化测试：

```powershell
.\.venv\Scripts\python -m pytest backend\tests\test_mcp.py -q
```

覆盖 stdio 子进程发现工具、协议结构化返回、搜索→导入→问答参数传递、摘要/对比证据保留、输入校验、错误脱敏、不自动重试和取消请求。流程测试使用模拟 HTTP 响应，不代表真实来源下载或模型质量通过。没有安装可选 MCP 依赖时该测试文件跳过，原有后端测试仍可运行。

## 边界

- 不开放任意文件读取、任意 URL 下载、删除、Shell 或密钥读取工具；PDF 下载继续使用后端公网地址校验。论文正文视为资料，不是执行指令。
- 只读工具使用 MCP 只读标注；搜索、导入及生成工具标注可能写入记录。工具描述会说明模型费用，但标注不是计费授权或预算执行器。
- 每次工具调用有 HTTP 超时，不自动重试。取消会中止适配端 HTTP 请求；后端沿用断连检测。已被供应商接受的请求仍可能计费，已保存结果不会回滚。
- 外部客户端自主控制循环，**不继承网页研究任务助手的整任务调用预算**；请在客户端限制调用次数。当前 MCP 没有多 Agent 编排。
- 日志输出到 stderr，记录工具名、状态和耗时，不主动记录正文、提问或密钥。stdout 专用于 MCP 协议。
- 之前的摘要偶发格式失败仍待定位；MCP 原样遵循后端校验，不掩盖失败或降低证据要求。

参考：[官方 Python SDK v1 文档](https://py.sdk.modelcontextprotocol.io/v1/)。
