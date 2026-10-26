<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { answerMarkdown, downloadMarkdown } from './markdown'
type Evidence = { id: string; page: number; section: string; text: string }
type Answer = { id: string; question: string; supported: boolean; reason: string; claims: { text: string; evidence: Evidence[] }[]; retrieval: string; created_at: string }
const props = defineProps<{ readingId: string; paperTitle: string; sourceUrl?: string | null; configured: boolean; disabled: boolean }>()
const question = ref(''), history = ref<Answer[]>([]), busy = ref(false), error = ref(''), notice = ref('')
const semantic = ref(false), partial = ref(false), loading = ref(true)
let controller: AbortController | null = null
const lifecycle = new AbortController()
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, options)
  const result = await response.json()
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '问答请求无效，请检查问题后重试。')
  return result
}
onMounted(async () => {
  try {
    const [state, saved] = await Promise.all([
      request<{ embedding_configured?: boolean; embedding_partial?: boolean }>('/health', { signal: lifecycle.signal }),
      request<Answer[]>(`/readings/${props.readingId}/questions`, { signal: lifecycle.signal }),
    ])
    semantic.value = !!state.embedding_configured; partial.value = !!state.embedding_partial; history.value = saved
  } catch { if (!lifecycle.signal.aborted) error.value = '问答记录加载失败，请刷新页面重试。' }
  finally { loading.value = false }
})
onBeforeUnmount(() => { lifecycle.abort(); controller?.abort() })
async function ask() {
  if (busy.value || props.disabled) return
  const text = question.value.trim()
  if (text.length < 2) { error.value = '请填写完整的问题。'; return }
  busy.value = true; error.value = ''; notice.value = ''
  const active = new AbortController(); controller = active
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; active.abort() }, 250000)
  try {
    const answer = await request<Answer>(`/readings/${props.readingId}/questions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: active.signal,
      body: JSON.stringify({ question: text }),
    })
    history.value = [answer, ...history.value].slice(0, 50)
    question.value = ''
  } catch (e) {
    if (active.signal.aborted) notice.value = timedOut ? '问答等待超时，请稍后重试。' : '已取消本次问答。'
    else error.value = e instanceof Error ? e.message : '问答失败。'
  } finally { clearTimeout(timer); busy.value = false; controller = null }
}
</script>

<template>
  <section class="paper-questions" aria-labelledby="questions-title">
    <h3 id="questions-title">问这篇论文</h3>
    <p class="qa-help" v-if="semantic">语义检索已启用</p>
    <p class="qa-help" v-else-if="partial">Embedding 配置不完整，请补全或全部清空后重启后端。</p>
    <p class="qa-help" v-else>关键词检索</p>
    <form @submit.prevent="ask">
      <label>论文问题<textarea v-model="question" rows="3" maxlength="1500" :disabled="busy || disabled" placeholder="这篇论文的方法有哪些局限？" /></label>
      <div class="qa-actions"><button type="submit" class="primary" :disabled="busy || disabled || loading || !configured || partial">{{ busy ? '正在查找依据并回答…' : '提问' }}</button><button v-if="busy" type="button" class="secondary" @click="controller?.abort()">取消问答</button></div>
    </form>
    <p v-if="!configured" class="qa-help">请先配置云端模型，已有问答记录仍可查看。</p>
    <p v-if="error" class="message error" role="alert">{{ error }}</p><p v-if="notice" class="message" role="status">{{ notice }}</p>
    <div aria-live="polite"><article v-for="item in history" :key="item.id" class="qa-answer">
      <button type="button" class="secondary" @click="downloadMarkdown(paperTitle + '-问答-' + item.id, answerMarkdown({ id: readingId, title: paperTitle, source_url: sourceUrl }, item))">导出问答 Markdown</button>
      <h4>{{ item.question }}</h4><p class="qa-help">{{ item.retrieval === 'semantic' ? '语义检索' : '关键词检索' }} · {{ new Date(item.created_at).toLocaleString('zh-CN') }}</p>
      <p v-if="!item.supported" class="insufficient">依据不足</p><p v-if="item.reason" class="answer-text">{{ item.reason }}</p>
      <div v-for="(claim, index) in item.claims" :key="index"><p class="answer-text">{{ claim.text }}</p><details><summary>查看回答依据</summary><blockquote v-for="evidence in claim.evidence" :key="evidence.id"><small>PDF 第 {{ evidence.page }} 页 · {{ evidence.section }}</small><p>{{ evidence.text }}</p></blockquote></details></div>
    </article></div>
  </section>
</template>

<style scoped>
.paper-questions{border-top:1px solid #dce3dd;margin-top:32px;padding-top:22px}.paper-questions h3{font-size:20px}.qa-help{font-size:12px;line-height:1.9;color:#728168}.paper-questions textarea{margin:8px 0}.qa-actions{display:flex;gap:12px;margin-top:14px;flex-wrap:wrap}.qa-answer{padding:20px;margin:20px 0;background:#f6f8f2;border:1px solid #e3e9e2;border-radius:12px;overflow-wrap:anywhere}.qa-answer h4{font-size:16px;line-height:1.7;margin:0}.answer-text{font-size:16px;white-space:pre-wrap;line-height:1.9}.qa-answer summary{cursor:pointer;color:#52745e}.qa-answer blockquote{margin:16px 0;padding-left:14px;border-left:3px solid #b9cbb0;font-size:13px;line-height:1.9;white-space:pre-wrap}.qa-answer small{color:#697e61}.insufficient{color:#9b6134;font-weight:600}@media(max-width:700px){.qa-answer{padding:14px}}
</style>
