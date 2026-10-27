<script setup lang="ts">
import { uniquePDFs } from './library'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
type Evidence = { id: string; page: number; text: string }
type Run = { id: string; goal: string; status: string; message: string; created_at: string;
  mode?: 'single' | 'multi';
  events: { tool: string; status: string; message: string; role?: string }[];
  papers: { paper_id: string; title: string; url: string; public_pdf: boolean }[];
  readings: { id: string; title: string }[];
  comparison: null | { papers: { id: string; title: string }[]; dimensions: string[];
    cells: { reading_id: string; dimension: string; text: string; supported: boolean; evidence: Evidence[] }[] };
  answers: { id: string; question: string; reason: string; claims: { text: string; evidence: Evidence[] }[] }[] }
const props = defineProps<{ configured: boolean }>()
const goal = ref(''), error = ref(''), working = ref(false), loading = ref(true), run = ref<Run | null>(null), history = ref<Run[]>([])
const mode = ref<'single' | 'multi'>('single')
const modes = { single: '单 Agent', multi: '多 Agent' }
const roles: Record<string, string> = { single: '单 Agent', coordinator: '协调 Agent', retrieval: '检索 Agent', analysis: '分析 Agent' }
const library = ref<{ id: string; title: string; kind: string; content_key?: string | null }[]>([]), selected = ref<string[]>([])
const selectablePapers = computed(() => uniquePDFs(library.value, selected.value))
const running = computed(() => !!run.value && ['queued', 'running'].includes(run.value.status))
const statuses: Record<string, string> = { queued: '等待执行', running: '执行中', completed: '已结束', partial: '部分完成', failed: '执行失败', limited: '达到上限', cancelled: '已取消', interrupted: '服务重启中断' }
const dimensions: Record<string, string> = { goal: '研究目标', method: '方法', experiments: '实验设置', results: '实验结果', limitations: '局限' }
let timer: ReturnType<typeof setTimeout> | undefined
let epoch = 0
const lifecycle = new AbortController()
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, { ...options, signal: lifecycle.signal })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '任务请求无效。')
  return data
}
async function refreshLibrary() {
  try { library.value = await api('/readings') }
  catch { if (!lifecycle.signal.aborted) error.value = '论文列表加载失败。' }
}
function schedule() {
  clearTimeout(timer)
  if (!running.value || lifecycle.signal.aborted) return
  const identifier = run.value!.id
  const version = epoch
  timer = setTimeout(async () => {
    try {
      const updated = await api<Run>('/agent/runs/' + identifier)
      if (run.value?.id === identifier && version === epoch) {
        run.value = updated
        history.value = history.value.map(item => item.id === identifier ? updated : item)
      }
    } catch { if (!lifecycle.signal.aborted) error.value = '任务状态暂时无法更新，正在重试。' }
    schedule()
  }, 1200)
}
onMounted(async () => {
  await refreshLibrary()
  try { history.value = await api('/agent/runs') }
  catch { if (!lifecycle.signal.aborted) error.value = '研究任务历史加载失败，请确认后端已更新。' }
  finally { loading.value = false }
})
onBeforeUnmount(() => { clearTimeout(timer); lifecycle.abort() })
async function start() {
  if (working.value || running.value || loading.value) return
  epoch++
  working.value = true; error.value = ''
  try {
    run.value = await api<Run>('/agent/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal: goal.value, reading_ids: selected.value, mode: mode.value }) })
    history.value = [run.value, ...history.value].slice(0, 20); schedule()
  } catch (e) { error.value = e instanceof Error ? e.message : '任务启动失败。' }
  finally { working.value = false }
}
async function cancel() {
  if (!run.value) return
  epoch++; clearTimeout(timer)
  working.value = true
  try {
    run.value = await api<Run>(`/agent/runs/${run.value.id}/cancel`, { method: 'POST' })
    history.value = history.value.map(item => item.id === run.value!.id ? run.value! : item)
    schedule()
  }
  catch { error.value = '取消请求失败，请重试。' }
  finally { working.value = false; schedule() }
}
function open(identifier: string) { epoch++; run.value = history.value.find(item => item.id === identifier) ?? null; schedule() }
function safeURL(value: string) { return /^https?:\/\//i.test(value) ? value : undefined }
</script>

<template>
  <section class="research-agent" aria-labelledby="agent-heading">
    <div class="module-heading"><div><div class="eyebrow">RESEARCH ASSISTANT</div><h2 id="agent-heading">研究任务助手</h2></div><span class="module-tag">Agent</span></div>
    <form @submit.prevent="start">
      <label class="agent-field"><span>执行模式</span><select v-model="mode" :disabled="running || working"><option value="single">单 Agent（默认）</option><option value="multi">多 Agent（协调、检索、分析）</option></select></label>
      <label>研究任务<textarea v-model="goal" rows="3" maxlength="3000" :disabled="running || working" placeholder="帮我找小模型调用工具的论文，选三篇并比较它们的方法。" /></label>
      <details><summary @click="refreshLibrary">使用已上传的 PDF（可选，最多 5 篇）</summary>
        <p v-if="!library.some(p => p.kind === 'pdf')" class="help">可先在下方上传 PDF，再展开这里刷新列表。</p>
        <label v-for="paper in selectablePapers" :key="paper.id" class="choice"><input v-model="selected" type="checkbox" :value="paper.id" :disabled="running || working || (selected.length >= 5 && !selected.includes(paper.id))">{{ paper.title }}</label>
      </details>
      <div class="actions"><button class="primary" :disabled="loading || !configured || working || running || goal.trim().length < 2">启动研究任务</button><button v-if="running" type="button" class="secondary" :disabled="working" @click="cancel">取消研究任务</button></div>
    </form>
    <p v-if="!configured" class="help">请先配置云端模型。</p>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <label v-if="history.length" class="agent-field"><span>研究任务历史</span><select :value="run?.id ?? ''" :disabled="running || working" @change="open(($event.target as HTMLSelectElement).value)"><option value="">选择历史研究任务</option><option v-for="item in history" :key="item.id" :value="item.id">{{ modes[item.mode ?? 'single'] }} · {{ item.goal }}</option></select></label>
    <article v-if="run" class="agent-result">
      <h3>{{ run.goal }}</h3><p role="status">{{ modes[run.mode ?? 'single'] }} · {{ statuses[run.status] ?? run.status }} · {{ run.message }}</p>
      <details open><summary>执行记录</summary><ol><li v-for="(event, index) in run.events" :key="index" :class="{ warning: event.status === 'error' || event.status === 'partial' }"><strong v-if="event.role">{{ roles[event.role] ?? event.role }} · </strong>{{ event.message }}</li></ol></details>
      <div v-if="run.papers.length"><h4>检索到的候选论文</h4><ul><li v-for="paper in run.papers" :key="paper.paper_id"><a :href="safeURL(paper.url)" target="_blank" rel="noopener noreferrer">{{ paper.title }}</a> · {{ paper.public_pdf ? '提供公开全文链接' : '未提供公开全文链接' }}</li></ul></div>
      <div v-if="run.readings.length"><h4>本任务可阅读的 PDF</h4><ul><li v-for="paper in run.readings" :key="paper.id">{{ paper.title }}</li></ul></div>
      <div v-for="answer in run.answers" :key="answer.id"><h4>{{ answer.question }}</h4><p v-if="answer.reason">{{ answer.reason }}</p><div v-for="(claim, index) in answer.claims" :key="index"><p>{{ claim.text }}</p><details><summary>查看问答依据</summary><blockquote v-for="e in claim.evidence" :key="e.id"><small>PDF 第 {{ e.page }} 页</small><p>{{ e.text }}</p></blockquote></details></div></div>
      <div v-if="run.comparison" class="table-scroll" role="region" aria-label="研究任务对比结果" tabindex="0"><table><caption>论文对比与证据</caption><thead><tr><th>维度</th><th v-for="p in run.comparison.papers" :key="p.id">{{ p.title }}</th></tr></thead><tbody><tr v-for="d in run.comparison.dimensions" :key="d"><th>{{ dimensions[d] }}</th><td v-for="p in run.comparison.papers" :key="p.id"><template v-for="cell in run.comparison.cells.filter(c => c.reading_id === p.id && c.dimension === d)" :key="cell.reading_id"><p>{{ cell.text }}</p><details v-if="cell.evidence.length"><summary>查看对比依据</summary><blockquote v-for="e in cell.evidence" :key="e.id"><small>{{ p.title }} · PDF 第 {{ e.page }} 页</small><p>{{ e.text }}</p></blockquote></details></template></td></tr></tbody></table></div>
    </article>
  </section>
</template>

<style scoped>
.agent-field{display:flex;flex-direction:column;align-items:flex-start;gap:8px;margin:16px 0}.agent-field select{display:block;margin:0;width:100%}

.research-agent{margin-top:28px;padding:28px;border:1px solid #dce3dd;border-radius:14px;background:white;min-width:0}.help{font-size:13px;line-height:1.9;color:#728168}textarea,select{width:100%;margin:10px 0 16px}.actions{display:flex;gap:12px;margin:18px 0;flex-wrap:wrap}.choice{display:flex;gap:10px;font-size:14px;margin:10px 0;overflow-wrap:anywhere}.choice input{width:auto}.agent-result{margin-top:24px;overflow-wrap:anywhere;line-height:1.9}.agent-result h3{font-size:18px}.agent-result li{margin:8px 0}.warning{color:#98653a}summary{cursor:pointer;color:#52745e}blockquote{margin:12px 0;padding-left:10px;border-left:3px solid #b9cbb0;font-size:13px}.table-scroll{max-width:100%;overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{min-width:220px;max-width:350px;padding:12px;border:1px solid #dce3dd;vertical-align:top;text-align:left}th{background:#eef3e9}th:first-child{min-width:90px}@media(max-width:700px){.research-agent{padding:18px}}
</style>
