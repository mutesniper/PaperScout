<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import PaperQuestions from './PaperQuestions.vue'
type Summary = { abstract: string; evidence: { page: number; quote: string }[]; model: string; created_at: string; scope: 'fulltext' | 'abstract' }
type Reading = { id: string; title: string; kind: 'pdf' | 'abstract'; page_count: number; warnings: string[]; document_summary: Summary | null }
type Entry = { id: string; title: string; kind: string }
const props = defineProps<{ configured: boolean }>()
const doc = ref<Reading | null>(null), library = ref<Entry[]>([])
const busy = ref(''), error = ref(''), notice = ref(''), panel = ref<HTMLElement | null>(null)
let controller: AbortController | null = null
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api/readings' + path, options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请重试。')
  return data
}
async function refresh() { library.value = await api<Entry[]>('') }
onMounted(() => refresh().catch(() => { error.value = '摘要记录加载失败，请确认后端已更新。' }))
onBeforeUnmount(() => controller?.abort())
async function run(label: string, action: (signal: AbortSignal) => Promise<void>) {
  if (busy.value) return
  busy.value = label; error.value = ''; notice.value = ''
  const active = new AbortController(); controller = active
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; active.abort() }, 1900000)
  try { await action(active.signal) }
  catch (e) { error.value = active.signal.aborted ? (timedOut ? '生成超时，请重试。' : '已取消；已上传文件和已有摘要仍保留。') : e instanceof Error ? e.message : '请求失败。' }
  finally { clearTimeout(timer); controller = null; busy.value = '' }
}
async function generate(signal: AbortSignal) {
  if (!doc.value) return
  const current = doc.value
  busy.value = current.kind === 'pdf' ? '正在综合全文生成摘要，长论文可能需要几分钟…' : '正在概括来源摘要…'
  current.document_summary = await api<Summary>(`/${current.id}/summary`, { method: 'POST', signal })
  notice.value = '摘要已生成并保存。'
}
async function received(data: Reading, signal: AbortSignal) {
  doc.value = data
  panel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  try { await refresh() } catch { notice.value = '文件已保存，历史列表刷新失败。' }
  if (signal.aborted) return
  if (props.configured) await generate(signal)
  else notice.value = '文件已保存。配置云端模型并重启后端后，可生成摘要。'
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > 20 * 1024 * 1024) { error.value = '文件超过 20 MB。'; return }
  await run('正在上传并提取 PDF 内容…', async signal => {
    await received(await api<Reading>('/upload?filename=' + encodeURIComponent(file.name), {
      method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: file, signal,
    }), signal)
  })
}
async function importPaper(recordId: string, paperId: string, mode: 'pdf' | 'abstract') {
  panel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  await run(mode === 'pdf' ? '正在获取公开 PDF…' : '正在获取来源摘要…', async signal => {
    await received(await api<Reading>('/import', { method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_id: recordId, paper_id: paperId, mode }) }), signal)
  })
}
defineExpose({ importPaper })
async function open(id: string) {
  if (id) await run('正在读取记录…', async signal => { doc.value = await api<Reading>('/' + id, { signal }) })
}
</script>

<template>
  <section ref="panel" class="summary-panel" aria-labelledby="summary-heading">
    <div class="summary-title"><div><div class="eyebrow">FROM PAPER TO INSIGHT</div><h2 id="summary-heading">论文全文摘要</h2></div><label class="secondary upload-label">上传 PDF<input aria-label="上传 PDF" type="file" accept="application/pdf,.pdf" :disabled="!!busy" @change="upload"></label></div>
    <p class="result-context">上传论文，获得一份涵盖研究问题、方法、主要结果与局限的全文摘要。支持 20 MB / 100 页以内的文字型 PDF。</p>
    <p class="result-context">{{ configured ? '上传后自动生成摘要，提取文本会发送给你配置的云端模型；长论文可能需要多次调用。' : '尚未配置模型：可以先上传，配置后再生成摘要。' }}</p>
    <label for="summary-library">最近摘要</label><select id="summary-library" :value="doc?.id ?? ''" :disabled="!!busy" @change="open(($event.target as HTMLSelectElement).value)"><option value="">选择已保存的论文</option><option v-for="item in library" :key="item.id" :value="item.id">{{ item.kind === 'pdf' ? '全文' : '仅来源摘要' }} · {{ item.title }}</option></select>
    <div v-if="busy" class="message loading" role="status">{{ busy }}<button @click="controller?.abort()">取消生成</button></div>
    <div v-if="error" role="alert" class="message error">{{ error }}</div><div v-if="notice" role="status" class="message">{{ notice }}</div>
    <template v-if="doc">
      <h3 class="paper-title">{{ doc.title }}</h3>
      <span class="access available">{{ doc.kind === 'pdf' ? `基于 PDF 全文 · ${doc.page_count} 页` : '仅基于来源摘要 · 未读取全文' }}</span>
      <p v-for="warning in doc.warnings" :key="warning" class="result-context">{{ warning }}</p>
      <div class="summary-actions"><button class="primary" :disabled="!!busy || !configured" @click="run('正在生成摘要…', generate)">{{ doc.document_summary ? '重新生成摘要' : doc.kind === 'pdf' ? '生成全文摘要' : '概括来源摘要' }}</button></div>
      <article v-if="doc.document_summary" class="document-summary">
        <h3>{{ doc.kind === 'pdf' ? '全文摘要' : '来源摘要概括' }}</h3>
        <p class="result-context">{{ doc.document_summary.model }} · {{ new Date(doc.document_summary.created_at).toLocaleString('zh-CN') }}</p>
        <p class="abstract-content">{{ doc.document_summary.abstract }}</p>
        <details><summary>查看摘要依据</summary><blockquote v-for="(item, index) in doc.document_summary.evidence" :key="index"><small>{{ doc.kind === 'pdf' ? `PDF 第 ${item.page} 页` : '来源摘要' }}</small><p>{{ item.quote }}</p></blockquote></details>
        <p class="result-context">依据引句已与提取文本核对。摘要由模型生成，重要结论仍建议对照论文确认。</p>
      </article>
      <p v-else class="result-context">{{ busy ? '正在处理，请稍候。' : '尚未生成全文摘要。旧版分章节总结不会当作全文摘要显示。' }}</p>
      <PaperQuestions v-if="doc.kind === 'pdf'" :key="doc.id" :reading-id="doc.id" :configured="configured" :disabled="!!busy" />
    </template>
  </section>
</template>

<style scoped>
.summary-panel{margin-top:30px;padding:28px;border:1px solid #dce3dd;border-radius:14px;background:white;scroll-margin-top:20px}.summary-title{display:flex;align-items:center;justify-content:space-between;gap:16px}.summary-title h2{margin:10px 0}.upload-label{position:relative;cursor:pointer;overflow:hidden;flex-shrink:0}.upload-label input{position:absolute;inset:0;opacity:0;width:100%;cursor:pointer}.summary-panel select{width:100%;min-width:0;margin:8px 0 16px}.paper-title{line-height:1.8;overflow-wrap:anywhere}.summary-actions{margin:22px 0}.document-summary{max-width:76ch;margin:24px auto;padding:24px;background:#fafbf7;border:1px solid #e3e9e2;border-radius:12px}.abstract-content{font-size:17px;line-height:2;white-space:pre-wrap;overflow-wrap:anywhere}.document-summary details{margin:24px 0;font-size:14px}.document-summary summary{cursor:pointer;color:#53785c}.document-summary blockquote{margin:18px 0;padding-left:16px;border-left:3px solid #b9cbb0;line-height:1.8;overflow-wrap:anywhere}.document-summary small{color:#728568}@media(max-width:700px){.summary-panel,.document-summary{padding:18px}.summary-title h2{font-size:20px}}
</style>
