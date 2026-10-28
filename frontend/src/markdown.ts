type Paper = { id: string; title: string; source_url?: string | null }
type Evidence = { page: number; text: string }
type Summary = { abstract: string; scope: string; model: string; created_at: string; evidence: { page: number; quote: string }[] }
type Answer = { question: string; supported: boolean; reason: string; created_at: string; claims: { text: string; evidence: Evidence[] }[] }
type Comparison = { created_at: string; papers: Paper[]; dimensions: string[]; cells: { reading_id: string; dimension: string; supported: boolean; text: string; evidence: Evidence[] }[] }
const labels: Record<string, string> = { goal: '研究目标', method: '方法', experiments: '实验设置', results: '实验结果', limitations: '局限' }
// 将来源和模型文本视为纯文本，不作为可执行的 HTML 或 Markdown。
const escape = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/([\\`*_{}\[\]()#+.!|~-])/g, '\\$1')
const cell = (value: string) => escape(value).replace(/\r?\n/g, '<br>')
const quote = (value: string) => escape(value).split(/\r?\n/).map(line => '> ' + line).join('\n')
function paperInfo(paper: Paper) {
  let source = ''
  try { const url = new URL(paper.source_url || ''); if (['https:', 'http:'].includes(url.protocol)) source = `\n来源：<${url.href.replace(/>/g, '%3E')}>\n` } catch { /* 上传的 PDF 可能没有来源 URL */ }
  return `论文：${escape(paper.title)}\n\n记录 ID：${escape(paper.id)}\n${source}`
}
function evidence(items: Evidence[]) {
  return items.map(e => `PDF 第 ${e.page} 页\n\n${quote(e.text)}`).join('\n\n')
}
export function summaryMarkdown(paper: Paper, summary: Summary) {
  const full = summary.scope === 'fulltext'
  return `# ${full ? '全文摘要' : '来源摘要概括'}\n\n${paperInfo(paper)}\n模型：${escape(summary.model)}\n\n生成时间：${escape(summary.created_at)}\n\n${escape(summary.abstract)}\n\n## 原文依据\n\n` +
    summary.evidence.map(e => `${full ? `PDF 第 ${e.page} 页` : '来源摘要（未读取全文）'}\n\n${quote(e.quote)}`).join('\n\n') + '\n'
}
export function answerMarkdown(paper: Paper, answer: Answer) {
  return `# 论文问答\n\n${paperInfo(paper)}\n生成时间：${escape(answer.created_at)}\n\n## 问题\n\n${escape(answer.question)}\n\n## 回答\n\n` +
    (answer.supported ? '' : '依据不足\n\n') + (answer.reason ? escape(answer.reason) + '\n\n' : '') +
    answer.claims.map(c => `${escape(c.text)}\n\n${evidence(c.evidence)}`).join('\n\n') + '\n'
}
export function comparisonMarkdown(result: Comparison, library: Paper[] = []) {
  const papers = result.papers.map(p => ({ ...library.find(x => x.id === p.id), ...p }))
  const rows = result.dimensions.map(d => '| ' + cell(labels[d] || d) + ' | ' + papers.map(p => {
    const found = result.cells.find(c => c.reading_id === p.id && c.dimension === d)
    return found ? cell((found.supported ? '' : '依据不足：') + found.text) : '未提供结果'
  }).join(' | ') + ' |')
  return `# 多篇论文对比\n\n生成时间：${escape(result.created_at)}\n\n` + papers.map(paperInfo).join('\n') +
    '\n| 维度 | ' + papers.map(p => cell(p.title)).join(' | ') + ' |\n| --- | ' + papers.map(() => '---').join(' | ') + ' |\n' + rows.join('\n') +
    '\n\n## 对比依据\n\n' + result.cells.map(c => `### ${escape(papers.find(p => p.id === c.reading_id)?.title || c.reading_id)} · ${escape(labels[c.dimension] || c.dimension)}\n\n${evidence(c.evidence) || '无引用依据。'}`).join('\n\n') +
    '\n\n不同数据集和实验条件的结果不能直接比较优劣；重要结论请核对原文。\n'
}
export function downloadMarkdown(name: string, content: string) {
  const filename = name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/g, '').slice(0, 100) || 'PaperScout'
  const url = URL.createObjectURL(new Blob(['\uFEFF', content], { type: 'text/markdown;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename + '.md'
  document.body.appendChild(anchor); anchor.click(); anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
