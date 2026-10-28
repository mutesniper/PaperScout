# 启动与演示

## Docker 启动

安装并启动 Docker Desktop（Linux containers）。在项目根目录执行：

```powershell
# 仅在没有 .env 时创建，避免覆盖已有密钥
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 按需编辑 .env，再启动
docker compose up --build -d
docker compose ps
```

打开 http://localhost:8080 。只暴露本机 8080，前端 Nginx 将 `/api` 转发给后端，无需另开 8000 或 5173。首次构建需要下载镜像和依赖。

不配置密钥也能手动搜索、上传 PDF、查看已保存数据。生成摘要、问答、对比与研究任务需要完整 LLM 配置；三个 EMBEDDING 配置全部留空就是关键词检索；Semantic Scholar 密钥非必需。真实模型调用会产生服务费用，导出不会。

修改 `.env` 后执行 `docker compose up -d --force-recreate backend`；修改代码后执行 `docker compose up --build -d`。容器内的 localhost 是容器自己；如果模型服务在宿主机上，Docker Desktop 下使用 `host.docker.internal`，例如 `http://host.docker.internal:1234/v1`。

```powershell
docker compose logs --tail 80 backend
docker compose stop
# 再次启动，保留已有数据
docker compose up -d
```

论文文件、SQLite 和向量缓存保存在 `paperscout-data` 命名卷（实际名称有 Compose 项目前缀）。容器环境与本地 `backend/data/` 是两套独立数据，首次启动看不到本地旧历史是正常情况。`docker compose down` 保留数据卷；不要使用 `down -v`，它会删除容器保存的论文和历史。后端使用一个 worker，符合当前后台任务与 SQLite 的单用户设计。

`.env`、本地 PDF、数据库、评测输出均被 `.dockerignore` 排除，不写进镜像。运行时凭据通过 Compose 的 env_file 提供。此配置用于本地演示，未提供公网用户认证。

## 五分钟演示流程

1. **检索**：在手动检索输入 `tool calling`，选择 arXiv，返回 10 篇。展示相关性、年份排序和来源链接；不同来源可能限流。
2. **摘要**：选择可访问 PDF，或上传已准备的文字型 PDF。上传后配置了模型会自动生成摘要。展开原文依据，展示页码，再点击“导出摘要 Markdown”。
3. **问答**：问“这篇论文的方法是什么？”；展开引用原文。再问论文未提供的具体费用，观察是否明确说明依据不足。点击对应结果的“导出问答 Markdown”。
4. **对比**：准备第二篇 PDF，勾选两篇及“方法、局限”，生成对比。核对双方证据，点击“导出对比 Markdown”。历史结果需主动选择后展示。
5. **研究任务（可选）**：选择已有 PDF，要求“比较这两篇论文的方法和局限”。展示工具执行记录、取消与任务历史。自动搜索下载路径依赖网络，可另演示。

建议提前准备两篇已验证可解析的 PDF，完成真实摘要与问答后，现场展示保存结果并导出，避免演示时间受模型响应或下载限流影响。引用来自提取文本，仍需核对原论文；不同实验条件不直接比较优劣。

## 导出内容

- 摘要：标题、记录 ID、已有来源链接、模型、时间、全文/来源摘要范围及原文页码。
- 问答：当前单条问题与回答，包括拒答原因、逐条结论与引用原文。每条独立下载。
- 对比：论文信息、对比表格和逐项证据。缺少依据明确标注。旧记录无来源链接且库中无对应元数据时仅保留标题和 ID，不编造链接。

下载为 UTF-8 `.md`，直接使用浏览器当前已加载的数据，不触发生成接口，也不发送给第三方。可使用 VS Code Markdown 预览查看。没有来源 URL 的上传 PDF 只导出已有标题和记录 ID。历史未选择或尚无结果时不显示相应导出按钮。

## 验证

### 单 Agent / 多 Agent 手动验收

更新代码后重启本地后端并刷新前端；Docker 使用 `docker compose up --build -d --wait` 更新镜像。沿用原有模型配置，真实运行会产生模型费用。

1. 在“研究任务助手”确认执行模式默认“单 Agent”，选两篇已上传 PDF，输入“比较这两篇论文的方法和局限”，运行并检查结果。
2. 完成后选择“多 Agent”，选择同样两篇 PDF 并输入同一目标，运行。执行记录应出现协调与分析角色；已有资料足够时无需检索角色。检查两篇论文的对比依据归属与页码，不只看任务结束状态。
3. 如需验证三角色链路，单独新建多 Agent 任务，不选已有 PDF，输入“搜索 TinyAgent，获取其公开 PDF，说明 ToolRAG 如何选择工具并给出依据”。观察协调、检索和分析记录；网络故障或预算耗尽应明确显示，不将缺失全文视为成功。
4. 执行时模式选择禁用；可取消任务。刷新后默认不展示结果，选择历史应恢复任务模式、角色记录及已有结果。旧历史显示单 Agent。

对照时保持任务、论文、网络和模型配置一致，同时检查结果质量、耗时和调用量。已有真实对照及失败记录见[Agent 评测报告](../evaluation/AGENT_EVALUATION_RESULTS.md)。

### Docker 与导出

```powershell
docker compose config --quiet
# 前端页面与后端代理都应返回成功
Invoke-WebRequest http://localhost:8080 -UseBasicParsing
Invoke-RestMethod http://localhost:8080/api/health
```

在网页下载三类 Markdown，检查中文、表格、页码和依据；导出前后应没有新增模型调用。重启容器后检查已上传论文仍存在。原生开发启动和完整评测命令见 README 与 `docs/evaluation/EVALUATION.md`。

若公开 PDF 下载提示 Fake-IP，应修复代理 DNS 配置或手动上传，容器化不会绕过公网地址检查。8080 被占用时修改 compose.yaml 的主机端口，例如 `127.0.0.1:8081:80`。
