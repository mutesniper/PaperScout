<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import GoalExplorer from './GoalExplorer.vue'
type Plan = { queries: string[]; year_from: number | null; year_to: number | null }
type SearchContext = { goal: string; topic: string; reason: string }
type Sort = 'relevance' | 'newest' | 'oldest' | 'citations'
type Paper = { publication_date?: string | null; citation_count?: number | null; citation_source?: string | null; id: string; title: string; authors: string[]; year: number | null; abstract: string; doi: string | null; sources: string[]; url: string; fulltext_url: string | null; access: string; evidence: string }
type Result = { id: string; created_at: string; request: { plan: Plan; context?: SearchContext | null; sources: string[]; limit: number; sort?: Sort }; candidate_count?: number | null; ranking_note?: string; warnings?: string[]; papers: Paper[]; sources: { source: string; status: string; count: number; message: string; cached: boolean }[] }
type History = { id: string; created_at: string; queries: string[]; count: number }
const queries = ref(''), yearFrom = ref(''), yearTo = ref('')
const sources = ref(['arxiv', 'semantic_scholar']), limit = ref(10)
const searchContext = ref<SearchContext | null>(null), analysisBusy = ref(false)
const sort = ref<Sort>('relevance')
const sortLabels: Record<Sort, string> = { relevance: '相关性优先', newest: '最新发表', oldest: '最早发表', citations: '引用量最高' }
const busy = ref(''), error = ref(''), notice = ref(''), result = ref<Result | null>(null)
const history = ref<History[]>([]), configured = ref(false), model = ref(''), onlyOpen = ref(false)
let controller: AbortController | null = null
const labels: Record<string, string> = { arxiv: 'arXiv', semantic_scholar: 'Semantic Scholar', open_preprint: '公开预印本', open_fulltext: '公开全文链接', abstract_only: '已获取摘要', unknown: '全文状态未知' }
const visiblePapers = computed(() => result.value?.papers.filter(p => !onlyOpen.value || p.fulltext_url) ?? [])
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求内容不符合要求，请检查关键词与年份。')
  return data
}
async function loadHistory() { history.value = await api<History[]>('/history') }
onMounted(async () => {
  try { const state = await api<{ model_configured: boolean; model: string | null }>('/health'); configured.value = state.model_configured; model.value = state.model ?? ''; await loadHistory() }
  catch { error.value = '暂时无法连接服务，请确认后端已启动。' }
})
function cancel() { controller?.abort() }
async function run<T>(label: string, path: string, body?: unknown): Promise<T | undefined> {
  if (busy.value || analysisBusy.value) return
  busy.value = label; error.value = ''; notice.value = ''
  const active = new AbortController(); controller = active
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; active.abort() }, 100000)
  try { return await api<T>(path, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined, signal: active.signal }) }
  catch (e) { if (active.signal.aborted) notice.value = timedOut ? '等待超时，请缩小范围后重试。' : '已取消本次请求。'; else error.value = e instanceof Error ? e.message : '请求失败，请重试。' }
  finally { clearTimeout(timer); busy.value = ''; controller = null }
}
async function exploreKnowledge(payload: { plan: Plan; context: SearchContext }) {
  searchContext.value = payload.context
  queries.value = payload.plan.queries.join('\n')
  yearFrom.value = String(payload.plan.year_from ?? '')
  yearTo.value = String(payload.plan.year_to ?? '')
  await search()
  document.querySelector('.results')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
async function search() {
  const words = queries.value.split('\n').map(v => v.trim()).filter(Boolean)
  if (!words.length || words.length > 3 || words.some(v => v.length > 250) || !sources.value.length) { error.value = '请填写 1–3 行检索词（每行最多 250 字符），并选择至少一个来源。'; return }
  const from = yearFrom.value ? Number(yearFrom.value) : null, to = yearTo.value ? Number(yearTo.value) : null
  if ([from, to].some(y => y !== null && (!Number.isInteger(y) || y < 1900 || y > 2100)) || (from && to && from > to)) { error.value = '请填写 1900–2100 范围内的有效年份。'; return }
  const data = await run<Result>('正在检索论文，部分来源可能需要等待…', '/search', { plan: { queries: words, year_from: from, year_to: to }, context: searchContext.value, sources: sources.value, limit: limit.value, sort: sort.value })
  if (data) { result.value = data; onlyOpen.value = false; try { await loadHistory() } catch { notice.value = '检索已完成，历史列表刷新失败。' } }
}
async function openRecord(id: string) {
  const data = await run<Result>('正在读取记录…', '/history/' + id)
  if (data) { result.value = data; searchContext.value = data.request.context ?? null; queries.value = data.request.plan.queries.join('\n'); yearFrom.value = String(data.request.plan.year_from ?? ''); yearTo.value = String(data.request.plan.year_to ?? ''); sources.value = data.request.sources; limit.value = data.request.limit; sort.value = data.request.sort ?? 'relevance'; onlyOpen.value = false; notice.value = '正在查看历史快照，全文状态以当时查询结果为准。' }
}
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="PaperScout 首页"><span class="brand-mark">P<span>↗</span></span>PaperScout</a>
      <div class="nav-active"><span>⌕</span> 论文探索 <small>01</small></div>
      <div class="history-head">最近检索 <span>{{ history.length }}</span></div>
      <p v-if="!history.length" class="empty-history">你的探索记录会保存在这里。</p>
      <button v-for="item in history" :key="item.id" class="history-item" :disabled="!!busy || analysisBusy" @click="openRecord(item.id)">
        <span>{{ item.queries.join(' · ') }}</span><small>{{ new Date(item.created_at).toLocaleDateString('zh-CN') }} · {{ item.count }} 篇</small>
      </button>
      <div class="sidebar-footer"><span class="status-dot"></span> 第一阶段 · 检索工作台<p>让每一个想法，都有迹可循。</p></div>
    </aside>
    <main>
      <header class="topbar"><span>你的研究，从这里开始</span><span class="model-state">{{ configured ? model : '手动检索可用' }}</span></header>
      <section class="intro"><div class="eyebrow">A CLEARER PATH TO RESEARCH</div><h1>好想法，值得找到好论文<span>。</span></h1><p>把你的想法拆成可学习的知识与实现路线，再找到相关论文。</p></section>
      <section class="workspace">
        <div class="search-panel">
          <GoalExplorer :configured="configured" :disabled="!!busy" @busy="analysisBusy = $event" @explore="exploreKnowledge" />
          <div class="divider"></div>
          <div class="section-title"><span class="step">02</span><h2>论文检索工作台</h2><span class="optional">支持手动检索</span></div>
          <p v-if="searchContext" class="message">当前知识点：{{ searchContext.topic }}<br>目标：{{ searchContext.goal }}<br>{{ searchContext.reason }}<button class="secondary" @click="searchContext = null">切换为独立检索</button></p>
          <label for="queries">检索词 <small>最多 3 行，合并为一次查询；建议使用英文关键词</small></label>
          <textarea id="queries" v-model="queries" :disabled="!!busy || analysisBusy" placeholder="small language models tool use" rows="3"></textarea>
          <div class="filters"><div><label for="from">起始年份</label><input id="from" v-model="yearFrom" :disabled="!!busy || analysisBusy" type="number" min="1900" max="2100" placeholder="不限"></div><div><label for="to">结束年份</label><input id="to" v-model="yearTo" :disabled="!!busy || analysisBusy" type="number" min="1900" max="2100" placeholder="不限"></div><div><label for="limit">返回前多少篇</label><select id="limit" v-model="limit" :disabled="!!busy || analysisBusy"><option :value="5">5 篇</option><option :value="10">10 篇</option><option :value="20">20 篇</option><option :value="50">50 篇</option><option :value="100">100 篇</option></select></div></div>
          <div class="filters"><div><label for="sort">排序方式</label><select id="sort" v-model="sort" :disabled="!!busy || analysisBusy"><option v-for="(label, value) in sortLabels" :key="value" :value="value">{{ label }}</option></select></div></div>
          <p class="result-context">先获取候选、去重排序，再返回前 N 篇。时间与引用量排序仅限本次候选；引用量未知的论文排在最后。</p>
          <fieldset :disabled="!!busy || analysisBusy"><legend>论文来源</legend><label class="source-option"><input v-model="sources" type="checkbox" value="arxiv">arXiv <small>预印本</small></label><label class="source-option"><input v-model="sources" type="checkbox" value="semantic_scholar">Semantic Scholar <small>学术索引</small></label></fieldset>
          <div class="search-footer"><small>结果来自真实论文来源，自动合并重复记录。</small><button class="primary" :disabled="!!busy || analysisBusy" @click="search">开始探索 <span>→</span></button></div>
        </div>
        <aside class="guide"><div class="guide-icon">✳</div><h3>从想法到知识。</h3><p>不用先知道专业术语。描述目标，梳理可能需要的知识，再点击感兴趣的知识点查论文。</p><ol><li><b>从目标出发</b><span>先说明你想解决什么问题。</span></li><li><b>选择知识点</b><span>编辑建议的搜索词，查找论文。</span></li><li><b>回到原始来源</b><span>每条结果都保留来源链接。</span></li></ol><div class="guide-note">当前版本提供检索与摘要展示。全文阅读和证据问答将在后续阶段加入。</div></aside>
      </section>
      <div v-if="busy" class="message loading" role="status"><span>{{ busy }}</span><button @click="cancel">取消请求</button></div>
      <div v-if="error" class="message error" role="alert">{{ error }}</div><div v-if="notice" class="message" role="status">{{ notice }}</div>
      <section class="results" aria-live="polite">
        <div class="results-head"><h2>探索结果 <span>{{ result ? visiblePapers.length : '—' }}</span></h2><label v-if="result" class="open-filter"><input v-model="onlyOpen" type="checkbox">仅显示有公开全文链接的论文</label></div>
        <template v-if="result"><p v-if="result.request.context" class="message">为“{{ result.request.context.topic }}”找到的候选论文 · 目标：{{ result.request.context.goal }}<br>结果依据检索词匹配，尚未逐篇核验是否适合这条路线。</p><p class="result-context">{{ result.request.plan.queries.join(' / ') }} · {{ new Date(result.created_at).toLocaleString('zh-CN') }}</p><p class="result-context" v-if="result.candidate_count != null">{{ sortLabels[result.request.sort ?? 'relevance'] }} · 去重后 {{ result.candidate_count }} 篇候选 · 返回 {{ result.papers.length }} 篇</p><p v-else class="result-context">旧版历史快照，保留原始结果顺序。</p><p class="result-context">{{ result.ranking_note }}</p><p v-for="warning in result.warnings ?? []" :key="warning" class="message">{{ warning }}</p><div class="source-status"><div v-for="s in result.sources" :key="s.source" :class="['source-report', s.status]"><b>{{ labels[s.source] }}</b> · {{ s.status === 'error' ? '检索失败' : `${s.count} 篇${s.status === 'partial' ? ' · 部分成功' : ''}` }}{{ s.cached ? ' · 缓存' : '' }}<p v-if="s.message">{{ s.message }}</p></div></div></template>
        <div v-if="!result" class="empty"><span>⌕</span><h3>下一篇值得读的论文，正在等你。</h3><p>填写关键词并开始探索，结果会出现在这里。</p></div>
        <div v-else-if="!visiblePapers.length" class="empty"><h3>{{ result.sources.every(s => s.status === 'error') ? '论文来源暂时不可用' : '没有符合当前条件的论文' }}</h3><p>可以调整关键词、放宽年份，或稍后重试。</p></div>
        <article v-for="(paper, index) in visiblePapers" :key="paper.id" class="paper"><div class="paper-index">{{ String(index + 1).padStart(2, '0') }}</div><div class="paper-body"><div class="paper-meta"><span>{{ paper.publication_date ?? paper.year ?? '日期未知' }}</span><span>{{ paper.sources.map(s => labels[s]).join(' · ') }}</span><span>引用 {{ paper.citation_count ?? '未知' }}{{ paper.citation_source === 'semantic_scholar' ? ' · Semantic Scholar' : '' }}</span><span :class="['access', { available: !!paper.fulltext_url }]">{{ labels[paper.access] }}</span></div><h3><a :href="paper.url" target="_blank" rel="noopener noreferrer">{{ paper.title }} ↗</a></h3><p class="authors">{{ paper.authors.slice(0, 6).join(', ') }}{{ paper.authors.length > 6 ? ' 等' : '' }}</p><details><summary>{{ paper.abstract ? '查看来源摘要' : '来源未提供摘要' }}</summary><p class="abstract">{{ paper.abstract || '暂无摘要。' }}</p></details><p class="evidence">{{ paper.evidence }}</p><div class="paper-links"><a :href="paper.url" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a><a v-if="paper.fulltext_url" :href="paper.fulltext_url" target="_blank" rel="noopener noreferrer">打开全文链接 ↗</a><small v-if="paper.doi">DOI: {{ paper.doi }}</small></div></div></article>
      </section>
      <footer>PaperScout <span>从想法到证据，一步一步。</span></footer>
    </main>
  </div>
</template>
