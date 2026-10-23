# PaperScout

从一个研究想法出发，寻找相关论文，提前了解公开全文线索。

当前完成检索与阅读两个阶段：云端模型生成可编辑检索计划，或手动检索 arXiv 与 Semantic Scholar；支持公开 PDF 获取、上传、全文摘要、可核对的原句依据及本地记录恢复。RAG 问答与自主 Agent 编排尚未实现。

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
| LLM_SUMMARY_MAX_TOKENS | 可选，摘要单次输出额度，默认 8192；截断时最多扩大额度重试一次（不超过 32768），更多输出可能增加费用 |
| SEMANTIC_SCHOLAR_API_KEY | 可选来源密钥；未配置时使用公共接口，可能被限流 |

没有模型密钥也可使用手动关键词检索。没有自动回退模型；未配置或请求失败时明确提示。当前模型接口支持检索计划与全文摘要，尚不支持工具循环或流式输出。上传与解析不需要模型密钥。

## 试用流程

1. 在检索词框输入 `small language models`，选择来源，开始检索。
2. 查看结果标题、来源摘要、原文链接和全文状态依据。
3. 修改年份或关键词，再次检索。
4. 从左侧最近检索读取历史快照。
5. 配置模型后，输入中文想法，生成计划；修改确认后再检索。

最多填写三行关键词，每行独立查询，每组每来源最多获取 100 篇；同来源的多组结果按排名交替合并、去重后保留前 100 篇。每个来源候选总量由后端固定为 100，不随关键词行数增加，也不提供用户配置；返回数量独立设置，默认 10、最多 100。候选先过滤、合并去重并排序，再截取前 N 篇；不足 N 篇时返回实际结果。arXiv 使用关键词 AND 查询，过窄时请减少关键词。日期筛选应用于来源返回的论文年份；arXiv 请求按提交日期筛选。

## 排序方式

来源支持相关性排序时，优先请求相关性顺序；来源没有该能力时，保留其默认顺序。每个来源对各行查询分别获取结果，交替合并去重后保留前 100 篇作为候选，不将用户选择的时间或引用量排序传给来源。

当前 arXiv 显式传入 `sortBy=relevance`，Semantic Scholar 使用 `/paper/search` 相关性搜索接口，无需另外传排序参数。今后接入不支持相关性排序的来源时，可以用默认顺序参与 RRF，但需要注明排序依据；RRF 只融合排名，不会将时间等默认排名转换成语义相关性。请求失败仍报告来源错误，不以移除排序参数作为通用重试方式。

| 方式 | 规则 |
| --- | --- |
| 相关性优先 | 单来源单查询保留来源顺序；多来源按 RRF 分数 `sum(1 / (60 + rank))` 降序排列 |
| 最新发表 / 最早发表 | 在本次候选内按日期降序 / 升序排列 |
| 引用量最高 | 在本次候选内按 Semantic Scholar 引用次数降序排列，未知值在已知值（包括 0）之后 |

单行查询保留来源排名；多行查询按各组第 1 篇、第 2 篇依次交替合并去重，避免第一行占满候选。部分查询失败时保留成功结果并提示；每组独立缓存。同一篇论文在同一来源只贡献一次 RRF 分数；重复记录合并时保留各来源排名。RRF 同分保留首次出现的顺序；日期、引用量同分则按相关性顺序排列。

arXiv 日期为首次提交日期，Semantic Scholar 日期为其记录的 publicationDate；同一论文合并时保留先遇到的有效日期。只有年份的论文按年份分组，排在同年精确日期之后；年份和日期都未知时排在最后。不会编造月日。

arXiv 不提供引用次数，只有同时匹配到 Semantic Scholar 候选时才能合并其引用数据，本版不额外批量补查。界面提示缺失比例；若全部未知，引用排序退化为相关性顺序。引用次数是来源元数据及缓存时的快照，不是实时统计。

时间和引用量排序只覆盖当前候选集，不代表全库排名。固定每来源 100 篇是检索范围与成本的取舍，不代表后续排名论文必然不相关，也不能保证找到特定经典论文。切换排序或返回条数后，点击“开始探索”应用；相同候选请求可复用缓存。

接口参数示例：`{"plan":{"queries":["transformer"]},"sources":["arxiv","semantic_scholar"],"limit":10,"sort":"relevance"}`。响应包含 `candidate_count`、`ranking_note`、`warnings`，论文包含日期、引用次数、来源排名及融合分数。

缓存保存原始候选，排序方式和返回条数变化后重新本地排序；候选数量与元数据版本进入缓存键。旧历史记录仍按原快照展示，不追溯重排。

## 全文状态含义

- 公开预印本：来源提供 arXiv 版本 PDF 链接。
- 公开全文链接：Semantic Scholar 元数据提供公开 PDF 地址，不保证它是出版最终版。
- 已获取摘要：已有摘要但未取得公开全文链接。
- 全文状态未知：现有元数据不足。

链接来自来源元数据；点击“获取公开 PDF”后才实际下载并验证。链接可能失效。没有链接不代表付费，网络失败也不代表付费。界面没有凭空判定“明确受限”的逻辑。

检索成功响应缓存 24 小时，包括真实空结果；失败不会缓存。检索记录和缓存保存在 `backend/data/paperscout.db`，历史接口返回最近 50 条。记录是查询当时的快照，不保证当前可访问性。

## 第二阶段：PDF 全文摘要

上传 PDF 后，配置模型时会自动综合全文生成一份摘要，覆盖研究问题、方法、主要结果与局限。未配置模型时先保存文件，配置并重启后端后点击“生成全文摘要”。页面不再展示全文、章节导航或逐页/逐章节摘要。检索结果仍支持获取公开 PDF 后生成全文摘要；“概括来源摘要”始终明确标记未读取全文。

短论文直接整体总结；长论文在后台按文本长度提取信息，再综合所有中间结果生成一份全文摘要，不展示分段结果，也不只截取开头。任一环节失败都不会保存部分结果冒充完整摘要。全文摘要与代表性原句证据一起保存。模型选择原文片段编号，程序据此取回原句与页码，再与提取文本核验；未知编号会被拒绝。已有摘要在重新生成失败时保持不变。旧版章节总结不迁移为全文摘要；可选择历史论文重新生成。

上传解析在本地完成；配置模型时，上传后自动将提取文本发送给该云端模型，长论文可能调用多次并产生相应费用。可取消生成。下载/解析限时约 50/40 秒，全文摘要最长等待 30 分钟；模型单次调用超时为 120 秒；检测到输出截断时最多扩大额度重试一次。

文件限制：20 MB、100 页、60 万提取字符。不支持加密 PDF 或纯扫描件 OCR。双栏、公式、表格和图片信息可能提取不完整；摘要以成功提取的文本为依据，不保证覆盖未提取的视觉内容。引句存在性核验不等于模型结论一定正确。

最近摘要可恢复文件记录及全文摘要；原文件仍保存在被 Git 忽略的 `backend/data/pdfs/`，记录存于 SQLite。未实现多用户隔离，不应直接公开部署。

公开下载仅使用检索结果中的全文链接，旧 HTTP 地址尝试升级 HTTPS，逐次校验重定向和公网地址，不绕过登录或付费墙。Fake-IP 代理地址可能被拒绝，此时可手动上传合法获取的 PDF。

主要接口：`POST /api/readings/upload?filename=...`（原始 PDF 请求体）、`POST /api/readings/import`、`GET /api/readings`、`GET /api/readings/{id}`、`POST /api/readings/{id}/summary`。旧的章节总结接口已移除。内部仍保留页码信息用于证据校验。

更新依赖：

```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-lock.txt
```

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
backend/app/storage.py SQLite 检索历史、阅读记录、全文摘要与缓存
backend/app/reading.py PDF 上传、公开下载、阅读与总结接口
backend/app/pdf_worker.py 隔离进程中的 PDF 页码与章节解析
frontend/src/ReadingPanel.vue 上传与全文摘要展示
backend/app/main.py    HTTP 接口与任务边界
backend/tests         确定性测试
docs/PLAN.md          分阶段计划
docs/PROGRESS.md      验证结果与下一步
```

本地服务使用单个进程，确保 arXiv 限流器对当前服务所有请求生效。不要启用多个 worker 绕过限流。每次查询有超时和有界重试；浏览器可取消检索，服务端检测断开后取消仍在运行的检索任务。某个来源失败会保留其他来源结果。

来源文档：[arXiv API](https://info.arxiv.org/help/api/user-manual.html)、[arXiv 调用限制](https://info.arxiv.org/help/api/tou.html)、[Semantic Scholar API](https://api.semanticscholar.org/api-docs/graph)。

多关键词不代表所有来源采用相同的语义评分：arXiv 对分词后的词项使用 AND，Semantic Scholar 由其接口解释联合查询。词项过多可能缩小匹配范围；并不保证无遗漏。两来源合计去重前最多 200 篇，最终仍按返回条数截取。


## 生成引用

每条检索结果提供“生成引用”：展开后选择 GB/T 7714—2015 在线资源参考格式或 BibTeX，可编辑引用日期和条目后复制。复制被浏览器拒绝时会选中文本，支持手动复制；不调用模型或额外联网。不会自动添加论文中的参考文献编号。

引用只使用已获取元数据。缺少作者或年份时显示提示；不编造期刊、卷期、页码。当前文本格式属于在线资源参考条目，作者保留来源原名，不保证符合所有期刊/学校的最终格式要求。arXiv 条目指向预印本版本，不混入可能属于正式发表版本的 DOI；BibTeX 使用通用 misc 类型并保留已有作者、年份、链接及 arXiv 编号。正式期刊版引用仍需补齐出版信息后核对。

格式参考：[GB/T 7714—2015 原文（清华大学图书馆）](https://lib.tsinghua.edu.cn/wj/GBT7714-2015.pdf)、[CTAN BibTeX 引用说明](https://ctan.org/help/references)。这里明确提供 2015 版参考格式，并非声明采用最新版本；请遵循投稿或学校要求。
