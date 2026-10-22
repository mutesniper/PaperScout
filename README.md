# PaperScout

从一个研究想法出发，寻找相关论文，提前了解公开全文线索。

当前完成范围为第一阶段：云端模型生成可编辑检索计划，或手动关键词检索；查询 arXiv 与 Semantic Scholar，合并重复记录，展示来源摘要和全文线索，保存检索历史。尚未包含 PDF 阅读、自动总结、RAG 或自主 Agent 循环。

## 本地运行（Windows PowerShell）

要求 Python 3.11+、Node.js 20.19+ 或 22.12+，以及可访问论文 API 的网络。

在项目根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements-lock.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

另开一个终端：

```powershell
cd frontend
npm ci
npm run dev
```

浏览器访问 http://127.0.0.1:5173 。接口文档：http://127.0.0.1:8000/docs 。仅建议在可信本地环境运行；当前未实现用户认证及多用户数据隔离。

## 配置

`.env` 位于项目根目录，仅由后端读取，不提交版本控制。修改后重启后端。

| 配置 | 说明 |
| --- | --- |
| LLM_BASE_URL | 支持 Chat Completions 格式的云端服务根地址，通常以 `/v1` 结尾；不要填写完整 `/chat/completions` 路径 |
| LLM_API_KEY | 服务密钥 |
| LLM_MODEL | 该服务支持的具体模型名称 |
| SEMANTIC_SCHOLAR_API_KEY | 可选来源密钥；未配置时使用公共接口，可能被限流 |

没有模型密钥也可使用手动关键词检索。没有自动回退模型；未配置或请求失败时明确提示。当前模型接口支持结构化检索计划，不宣称已经支持工具循环或流式输出。

## 试用流程

1. 在检索词框输入 `small language models`，选择来源，开始检索。
2. 查看结果标题、来源摘要、原文链接和全文状态依据。
3. 修改年份或关键词，再次检索。
4. 从左侧最近检索读取历史快照。
5. 配置模型后，输入中文想法，生成计划；修改确认后再检索。

每行关键词为一个查询（最多三行），每个来源每组查询最多 20 条；合并去重后总数可能超过单组上限。arXiv 使用关键词 AND 查询，过窄时请减少关键词。日期筛选应用于来源返回的论文年份；arXiv 请求按提交日期筛选。结果顺序为来源相关性顺序合并，不是经过跨来源相关性重排序的榜单。

## 全文状态含义

- 公开预印本：来源提供 arXiv 版本 PDF 链接。
- 公开全文链接：Semantic Scholar 元数据提供公开 PDF 地址，不保证它是出版最终版。
- 已获取摘要：已有摘要但未取得公开全文链接。
- 全文状态未知：现有元数据不足。

链接来自来源元数据，本阶段不下载验证 PDF；链接可能失效。没有链接不代表付费，网络失败也不代表付费。界面没有凭空判定“明确受限”的逻辑。

检索成功响应缓存 24 小时，包括真实空结果；失败不会缓存。检索记录和缓存保存在 `backend/data/paperscout.db`，历史接口返回最近 50 条。记录是查询当时的快照，不保证当前可访问性。

## 开发验证

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
cd frontend
npm run build
```

后端测试通过 `pytest.ini` 定位模块。单元和接口测试使用明确的模拟来源；真实接口验证情况见 [进度记录](docs/PROGRESS.md)。

前端浏览器测试（需要已安装 Chrome；先执行 `npm run build`）：

```powershell
npx playwright test
```

浏览器测试加载真实生产构建，并只在测试内部模拟 API 响应，不依赖本地端口。若未安装 Chrome，可安装 Playwright Chromium 并调整 `playwright.config.ts` 的浏览器配置。

真实来源冒烟测试（项目根目录；会实际访问两个论文来源）：

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python backend\tests\live_smoke.py
```

`requirements-lock.txt` 保存本次验证的具体 Python 依赖版本，`requirements.txt` 表示允许的版本范围；前端通过 `package-lock.json` 固定依赖。

## 架构与计划

```text
frontend/src          Vue 检索工作台
backend/app/schemas.py 请求与返回值校验
backend/app/models.py 云端模型适配器与统一协议
backend/app/providers.py 论文来源、限流、归一化、去重
backend/app/storage.py SQLite 历史与缓存
backend/app/main.py    HTTP 接口与任务边界
backend/tests         确定性测试
docs/PLAN.md          分阶段计划
docs/PROGRESS.md      验证结果与下一步
```

本地服务使用单个进程，确保 arXiv 限流器对当前服务所有请求生效。不要启用多个 worker 绕过限流。每次查询有超时和有界重试；浏览器可取消检索，服务端检测断开后取消仍在运行的检索任务。某个来源失败会保留其他来源结果。

来源文档：[arXiv API](https://info.arxiv.org/help/api/user-manual.html)、[arXiv 调用限制](https://info.arxiv.org/help/api/tou.html)、[Semantic Scholar API](https://api.semanticscholar.org/api-docs/graph)。
