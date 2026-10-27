<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { uniquePDFs } from './library'
import { comparisonMarkdown, downloadMarkdown } from './markdown'
type Paper = { id: string; title: string; kind: string; content_key?: string | null }
type Evidence = { id: string; page: number; text: string }
type Cell = { reading_id: string; dimension: string; supported: boolean; text: string; evidence: Evidence[] }
type Result = { id: string; created_at: string; papers: { id: string; title: string }[]; dimensions: string[]; cells: Cell[] }
const props = defineProps<{ library: Paper[]; configured: boolean; disabled: boolean }>()
const labels: Record<string, string> = { goal: '研究目标', method: '方法', experiments: '实验设置', results: '实验结果', limitations: '局限' }
const papers = computed(() => uniquePDFs(props.library, selected.value))
const selected = ref<string[]>([]), dimensions = ref(['goal', 'method', 'limitations'])
const history = ref<Result[]>([]), result = ref<Result | null>(null)
const busy = ref(false), error = ref(''), notice = ref('')
let active: AbortController | null = null
const lifecycle = new AbortController()
async function api(options: RequestInit = {}) {
  const response = await fetch('/api/comparisons', options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请选择 2～5 篇论文和至少一个对比维度。')
  return data
}
onMounted(async () => {
  try { history.value = await api({ signal: lifecycle.signal }) }
  catch { if (!lifecycle.signal.aborted) error.value = '对比历史加载失败，请刷新重试。' }
})
onBeforeUnmount(() => { lifecycle.abort(); active?.abort() })
async function compare() {
  if (busy.value || props.disabled) return
  busy.value = true; error.value = ''; notice.value = ''
  const controller = new AbortController(); active = controller
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, 610000)
  try {
    const answer: Result = await api({ method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reading_ids: selected.value, dimensions: dimensions.value }) })
    result.value = answer; history.value = [answer, ...history.value].slice(0, 20)
  } catch (e) {
    if (controller.signal.aborted) notice.value = timedOut ? '对比等待超时，请稍后重试。' : '已取消对比，已有结果仍保留。'
    else error.value = e instanceof Error ? e.message : '对比失败。'
  } finally { clearTimeout(timer); active = null; busy.value = false }
}
function cell(paper: string, dimension: string) {
  return result.value?.cells.find(c => c.reading_id === paper && c.dimension === dimension)
}
</script>

<template>
  <section class="comparison" aria-labelledby="comparison-title">
    <h3 id="comparison-title">多篇论文对比</h3>
    <p v-if="papers.length < 2" class="help">请先上传至少两篇 PDF，无需等待摘要生成成功。</p>
    <form @submit.prevent="compare">
      <fieldset :disabled="busy || disabled"><legend>选择对比论文（{{ selected.length }}/5）</legend>
        <label v-for="paper in papers" :key="paper.id" class="choice"><input v-model="selected" type="checkbox" :value="paper.id" :disabled="selected.length >= 5 && !selected.includes(paper.id)"><span>{{ paper.title }}</span></label>
      </fieldset>
      <fieldset :disabled="busy || disabled"><legend>对比维度</legend><div class="dimensions">
        <label v-for="(label, key) in labels" :key="key" class="choice"><input v-model="dimensions" type="checkbox" :value="key"><span>{{ label }}</span></label>
      </div></fieldset>
      <div class="actions"><button class="primary" type="submit" :disabled="busy || disabled || !configured || selected.length < 2 || selected.length > 5 || !dimensions.length">{{ busy ? '正在检索并对比…' : '生成对比' }}</button><button v-if="busy" type="button" class="secondary" @click="active?.abort()">取消对比</button></div>
    </form>
    <p v-if="!configured" class="help">请先配置云端模型，已有对比仍可查看。</p>
    <p v-if="error" role="alert" class="message error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <label v-if="history.length">对比历史<select :value="result?.id ?? ''" @change="result = history.find(r => r.id === ($event.target as HTMLSelectElement).value) ?? null"><option value="">选择历史对比结果</option><option v-for="item in history" :key="item.id" :value="item.id">{{ new Date(item.created_at).toLocaleString('zh-CN') }} · {{ item.papers.map(p => p.title).join(' / ') }}</option></select></label>
    <div v-if="result" class="table-scroll" tabindex="0" role="region" aria-label="论文对比结果"><table>
      <caption>基于检索证据的论文对比</caption>
      <thead><tr><th scope="col">维度</th><th v-for="paper in result.papers" :key="paper.id" scope="col">{{ paper.title }}</th></tr></thead>
      <tbody><tr v-for="dimension in result.dimensions" :key="dimension"><th scope="row">{{ labels[dimension] }}</th>
        <td v-for="paper in result.papers" :key="paper.id"><p :class="{ insufficient: !cell(paper.id, dimension)?.supported }">{{ cell(paper.id, dimension)?.text }}</p>
          <details v-if="cell(paper.id, dimension)?.evidence.length"><summary>查看对比依据</summary><blockquote v-for="evidence in cell(paper.id, dimension)?.evidence" :key="evidence.id"><small>{{ paper.title }} · PDF 第 {{ evidence.page }} 页</small><p>{{ evidence.text }}</p></blockquote></details>
        </td></tr></tbody>
    </table></div>
    <button v-if="result" type="button" class="secondary" @click="downloadMarkdown('PaperScout-对比-' + result.id, comparisonMarkdown(result, library))">导出对比 Markdown</button>
  </section>
</template>

<style scoped>
.comparison{border-top:1px solid #dce3dd;margin-top:32px;padding-top:22px;min-width:0}.help{font-size:13px;line-height:1.9;color:#728168}fieldset{border:1px solid #dce3dd;border-radius:8px;margin:16px 0;min-width:0;padding:12px}.choice{display:flex;align-items:flex-start;gap:8px;margin:10px 0;font-size:14px;overflow-wrap:anywhere}.choice input{width:auto;flex-shrink:0;margin-top:4px}.dimensions{display:flex;flex-wrap:wrap;gap:8px 22px}.actions{display:flex;gap:12px;margin:16px 0}.comparison select{width:100%;margin:10px 0}.table-scroll{overflow-x:auto;max-width:100%;margin-top:18px}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.8}caption{text-align:left;padding:8px}th,td{border:1px solid #dce3dd;padding:14px;vertical-align:top;text-align:left;min-width:220px;max-width:360px;overflow-wrap:anywhere}th{background:#eef3e9}th:first-child{min-width:90px}td p{white-space:pre-wrap}summary{cursor:pointer;color:#52745e}blockquote{margin:12px 0;padding-left:10px;border-left:3px solid #b9cbb0;font-size:12px}.insufficient{color:#98653a}
</style>
