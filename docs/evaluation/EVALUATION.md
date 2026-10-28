# 固定样本评测

## 真实论文：直接运行

已补充三篇本地已有 PDF 的 12 道标注题：Small but Significant（2505.08588v1，9 页）、TinyAgent（2409.00608v3，9 页）、Game Theory Meets Large Language Models（2502.09053v2，35 页）。全部提取文本参与检索，未只截取答案所在段落。题目及短参考证据见 `backend/evaluation/real_manifest.json`。

在项目根目录执行：

```powershell
.\.venv\Scripts\python backend/evaluation/prepare_real.py
.\.venv\Scripts\python backend/evaluation/run.py --dataset outputs/evaluation/real-papers.json
.\.venv\Scripts\python backend/evaluation/run.py --dataset outputs/evaluation/real-papers.json --live --semantic
```

前两条不调用模型；第三条发送完整分块文本到 Embedding 服务、检索片段到回答模型，产生费用。报告位于 `outputs/evaluation/`。

准备脚本只读本地论文库，以提取文本哈希匹配版本，不依赖上传时的随机 ID；文本版本不匹配时停止。完整文本和逐题输出只保存在被 Git 忽略的本地报告目录，不随仓库发布。其他电脑需要相同版本的 PDF 与提取结果；不可为了通过测试而跳过哈希校验。重新解析导致格式变化时，应重新核对原句、页码和标注，发布新的数据版本。

这些题由 Codex 阅读现有提取文本后标注，尚未经过独立人工复核；拒答题包括未报告的费用、未测试硬件和虚构模型，不能据此证明所有缺失信息场景可靠。自动命中率针对指定参考证据，不保证覆盖多问句的全部答案；应逐题核对回答完整性。不把这套开发样本称为独立盲测基准。

这一步评估 RAG 检索与单篇问答，不增加页面功能。论文搜索网站、PDF 解析、全文摘要、多篇对比及 Agent 任务完成率还不属于本套评测范围。

## 默认离线运行

在仓库根目录运行，不需要启动前后端，也不需要配置任何密钥：

```powershell
.\.venv\Scripts\python backend/evaluation/run.py
```

默认仅执行真实的关键词检索实现，不调用模型、不读取 `.env`、不访问外部服务。每次创建临时 SQLite，退出后清理，不修改实际论文、问答或任务历史。

报告写入 `outputs/evaluation/`：JSON 用于比较指标，Markdown 用于逐题核对。文件名带时间戳，包含数据 SHA256、Git 提交、工作区是否有改动、模式、模型名称、逐题耗时与服务返回的用量。该目录不纳入 Git。

## 可选真实模型评测

确认 `.env` 的三个 LLM 配置完整后：

```powershell
.\.venv\Scripts\python backend/evaluation/run.py --live
```

此命令会把每题检索到的原文发送给模型，可能收费。复用正式问答逻辑，包括输出结构、证据编号校验和拒答；最多每题一次生成，没有自动重试。不要把这次评测的用量混入日常摘要用量。

对比配置的 Embedding 检索：

```powershell
.\.venv\Scripts\python backend/evaluation/run.py --semantic
.\.venv\Scripts\python backend/evaluation/run.py --live --semantic
```

`--semantic` 显式启用已配置的三个 EMBEDDING 变量，会发送文档片段与问题并产生对应费用。没有此参数，即使 `.env` 配置了 Embedding 也使用关键词模式。每轮使用独立冷缓存，同轮同文档复用向量；首题耗时含索引构建，不应与后续题直接比较。

## 样本与范围

下文的 LLM 裁判也只评估这套单篇问答样本，不评估搜索、下载、多 Agent 协作或工具路由。

`backend/evaluation/fixtures.json` 是自编的两份**虚构论文材料**、九道固定题，不是真实论文基准，也不代表真实完整 PDF 表现。每份有九个片段，大于检索 top-6，避免整个材料都被直接送入模型。包含中文问题、方法、数值、局限、无相关信息、相关片段未提供答案以及提示注入。

自定义真实论文样本：复制该文件，替换 documents 和 cases，再传 `--dataset 路径`。documents 使用项目 Reading 格式的已提取文本；真实 PDF 的提取结果固定后再评测。每道题手动标注 reference_answer；可回答题须提供非空 evidence（page + 原文 quote），不可回答题 evidence 留空。脚本检查原句确实位于相应文档与页码，不接受虚构参考证据。参考句应短于一个检索片段且不跨分块边界。

建议人工阅读后加入至少三篇真实论文，覆盖短、中、长文与跨语言问题；记录论文链接、版本和授权来源。真实论文材料尚未标注验收，不能把当前结果包装为真实论文质量成绩。固定集可用于开发回归；最终展示成绩另留未用于调参的测试集。

## 指标解释

- hit_at_6：可回答题的前六个片段是否命中至少一条标注证据。
- evidence_recall_at_6：每题标注证据被覆盖的比例，再取平均。
- reciprocal_rank：首次命中的倒数排名，未命中计零。
- support_decision_correct：真实模型模式下，可回答/不可回答判断是否符合标注。
- refusal_correct：不可回答题是否正确拒答。无检索命中时正式接口直接拒答，此类题不是模型拒答能力的单独证明。
- citation_provenance_valid：引用来自已检索的本篇原文；**不是结论受到原文支持的语义评分**。
- seconds：每题整体耗时；用量按 generation/embedding 分开。服务未返回用量记 null，不估算成真实 Token，也不把推理子项重复加总。

失败样本保留在报告内，检索指标按零计、回答决策按错误计，不通过删除失败题提高成绩。没有适用题的指标是 null。退出码 1 表示存在运行错误；退出码 0 仅表示运行成功，不代表所有质量指标达标。

默认不调用裁判模型。JSON 的 manual_review 留空，Markdown 提供核对清单；启用以下可选裁判后仍保留人工审核，不把模型评分视作人工真值。

更换检索、提示词或模型后，用同一数据哈希与相同模式重新运行，对比命中率、拒答、失败数、耗时、Token，再审阅失败题。保留完整报告，不只记录平均分。

## 可选 LLM-as-a-Judge

在 `.env` 中配置独立的 OpenAI 兼容聊天接口（BASE_URL 通常以 `/v1` 结尾）：

```dotenv
JUDGE_BASE_URL=https://你的服务地址/v1
JUDGE_API_KEY=你的裁判服务密钥
JUDGE_MODEL=裁判模型名称
```

只有显式传入 `--judge` 才会调用裁判，且必须配合 `--live`。三项缺失会在模型调用前停止，不自动使用 `LLM_*` 作为替代。可以明确配置相同服务或密钥；建议选择与回答模型不同的模型，减少自评偏好，但不同模型也不能消除评分偏差。

```powershell
.\.venv\Scripts\python backend/evaluation/run.py --live --judge
# 同时评估语义检索；真实论文数据可另加 --dataset 参数
.\.venv\Scripts\python backend/evaluation/run.py --live --semantic --judge
```

每道成功执行的问答（含正常拒答）最多额外调用一次裁判，无自动重试。发送内容包括问题、参考答案、标注证据、实际检索片段和生成回答；不会发送整篇 PDF。参考答案仅发送给裁判，不加入回答模型的提示词。判断标准固定在 `backend/evaluation/judge.py`：

| 维度 | 0 分 | 1 分 | 2 分 |
| --- | --- | --- | --- |
| correctness 正确性 | 主要错误、编造，或可回答题拒答 | 部分正确，存在实质错误 | 主要事实与参考答案和标注证据一致 |
| completeness 完整性 | 未回答必要要点 | 覆盖部分要点 | 覆盖全部必要要点 |
| grounding 证据支持度 | 主要结论无引用支持或与引用矛盾 | 部分结论缺少支持 | 所有实质结论有对应引用支持 |

标注不可回答的题，诚实拒答且不编造可在前两项得满分。所有正常拒答的 grounding 均记 `null`（不适用），不会当作满分；可回答题因漏检而拒答时，正确性和完整性仍按未回答评估。裁判必须检查实际引用，不能以标注证据替代回答未引用的依据。

逐题 `judge` 保存分数、理由、状态、耗时与用量。顶层 `judge` 保存裁判模型、提示词版本和哈希、独立用量及汇总：`scored`、`errors`、`skipped`、各项均分 `mean` 和有效计数 `count`。回答失败记 skipped；裁判超时、返回格式不合法或输出截断记 error，不改写原问答的成功状态，不伪造分数。均分仅对成功评分且适用的项计算，必须连同总题数、失败数、跳过数一起解读。裁判用量不混入原有 generation/embedding 用量，服务未返回 Token 时记 `null`。原 `seconds` 仍仅是问答/检索耗时，裁判耗时在逐题 `judge.seconds` 中。

有裁判运行错误时 CLI 返回退出码 1；评分低但运行正常不会改变退出码。报告中 `manual_review` 始终留待人工填写。模型可能误判，也可能受材料中的提示注入影响；应抽查原文、引用与评分理由。此模块已通过模拟服务测试，真实调用状态及用量见[LLM 裁判评测记录](LLM_JUDGE_RESULTS.md)。
