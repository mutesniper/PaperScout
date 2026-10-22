<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
type Plan = { queries: string[]; year_from: number | null; year_to: number | null }
type Knowledge = { name: string; importance: 'core' | 'optional'; explanation: string; goal_connection: string; plan: Plan }
type Analysis = { goal: string; assumptions: string[]; questions: string[]; knowledge: Knowledge[]; steps: string[]; alternatives: string[] }
const props = defineProps<{ configured: boolean; disabled: boolean }>()
const emit = defineEmits<{ explore: [payload: { plan: Plan; context: { goal: string; topic: string; reason: string } }]; busy: [value: boolean] }>()
const goal = ref(''), clarification = ref(''), error = ref(''), loading = ref(false)
const analysis = ref<Analysis | null>(null), usedModel = ref(''), analyzedGoal = ref('')
let controller: AbortController | null = null
onBeforeUnmount(() => controller?.abort())
async function analyze() {
  if (loading.value || props.disabled) return
  if (goal.value.trim().length < 2) { error.value = '请描述你想实现的目标。'; return }
  error.value = ''; loading.value = true; emit('busy', true)
  const active = new AbortController(); controller = active
  let timeout = false
  const timer = setTimeout(() => { timeout = true; active.abort() }, 195000)
  try {
    const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: active.signal,
      body: JSON.stringify({ text: goal.value, clarification: clarification.value }) })
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查目标描述。')
    analysis.value = data.analysis; usedModel.value = data.model; analyzedGoal.value = goal.value
  } catch (e) { error.value = active.signal.aborted ? (timeout ? '分析超时，请稍后重试。' : '已取消目标分析。') : e instanceof Error ? e.message : '分析失败，请重试。' }
  finally { clearTimeout(timer); loading.value = false; emit('busy', false); controller = null }
}
function explore(point: Knowledge) {
  if (!point.name.trim() || !point.plan.queries[0]?.trim()) { error.value = '请填写知识点名称和检索词。'; return }
  emit('explore', { plan: JSON.parse(JSON.stringify(point.plan)), context: { goal: analysis.value!.goal, topic: point.name, reason: point.goal_connection } })
}
function addPoint() {
  analysis.value?.knowledge.push({ name: '新知识点', importance: 'optional', explanation: '由你补充的知识点', goal_connection: '', plan: { queries: [''], year_from: null, year_to: null } })
}
</script>

<template>
  <section class="goal-explorer">
    <div class="section-title"><span class="step">01</span><h2>告诉我，你想做成什么？</h2></div>
    <label for="goal-input">用自己的话描述目标，不需要先知道专业术语</label>
    <textarea id="goal-input" v-model="goal" :disabled="loading || disabled" maxlength="3000" rows="3" placeholder="例如：我想让一个小模型读懂我的需求，自动选择工具完成任务，但不知道需要学习哪些知识。"></textarea>
    <details class="goal-constraints"><summary>补充背景与限制（可选）</summary><label for="goal-clarification">已有基础、设备、预算，或对下方问题的回答</label><textarea id="goal-clarification" v-model="clarification" :disabled="loading || disabled" maxlength="3000" rows="2" placeholder="例如：会 Python，只有 8GB 显存，优先使用现有模型。"></textarea></details>
    <div class="plan-footer"><span>{{ configured ? '先梳理知识与路线，再选择值得查阅的论文。' : '目标分析需要配置云端模型；下方手动检索仍可使用。' }}</span><button class="primary" :disabled="!configured || loading || disabled" @click="analyze">{{ loading ? '正在分析目标…' : analysis ? '重新分析目标' : '分析目标' }}</button><button v-if="loading" class="secondary" @click="controller?.abort()">取消分析</button></div>
    <p v-if="loading" class="result-context" role="status">模型正在整理知识与路线，可能需要 1–3 分钟；格式不完整时会自动重试一次。可随时取消页面等待。</p>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <section v-if="analysis" class="roadmap">
      <p class="result-context">由 {{ usedModel }} 提出 · 初步建议，尚未经论文证据核验</p>
      <p v-if="goal !== analyzedGoal" class="message">目标已修改，下方仍是上次分析；请重新分析以更新路线。</p>
      <label for="understood-goal">对目标的理解（可修改）</label><textarea id="understood-goal" v-model="analysis.goal" :disabled="loading || disabled" maxlength="1000" rows="2"></textarea>
      <div v-if="analysis.assumptions.length"><h3>当前假设</h3><ul><li v-for="item in analysis.assumptions" :key="item">{{ item }}</li></ul></div>
      <div v-if="analysis.questions.length" class="clarification-box"><h3>这些信息会影响路线</h3><ul><li v-for="question in analysis.questions" :key="question">{{ question }}</li></ul><label for="answers">补充回答</label><textarea id="answers" v-model="clarification" :disabled="loading || disabled" maxlength="3000" rows="2"></textarea><button class="secondary" :disabled="loading || disabled" @click="analyze">根据回答更新路线</button><p>也可以先按当前假设探索下方知识点。</p></div>
      <h3>可能需要的知识</h3><p class="result-context">“核心”表示当前建议路线中的基础；“可选”按需求选择。可编辑检索词后查询。</p>
      <div class="knowledge-grid">
        <article v-for="(point, index) in analysis.knowledge" :key="index" class="knowledge-card">
          <div class="section-title"><span class="step">{{ point.importance === 'core' ? '核心' : '可选' }}</span><button class="remove-point" :disabled="loading || disabled" :aria-label="'删除知识点 ' + point.name" @click="analysis.knowledge.splice(index, 1)">删除</button></div>
          <label :for="'point-name-' + index">知识点名称</label><input :id="'point-name-' + index" v-model="point.name" :disabled="loading || disabled" maxlength="100">
          <p>{{ point.explanation }}</p><p><b>与你的目标有关：</b>{{ point.goal_connection || '请结合你的目标选择检索词。' }}</p>
          <label :for="'point-query-' + index">英文检索词（包含目标背景）</label><input :id="'point-query-' + index" v-model="point.plan.queries[0]" :disabled="loading || disabled" maxlength="250">
          <button class="secondary" :disabled="loading || disabled || goal !== analyzedGoal" @click="explore(point)">查找相关论文 →</button>
        </article>
      </div>
      <button class="secondary" :disabled="loading || disabled || analysis.knowledge.length >= 8" @click="addPoint">＋ 补充知识点</button>
      <h3>候选实现思路</h3><ol><li v-for="(step, index) in analysis.steps" :key="index">{{ step }}</li></ol>
      <div v-if="analysis.alternatives.length"><h3>其他可选路线与取舍</h3><ul><li v-for="item in analysis.alternatives" :key="item">{{ item }}</li></ul></div>
    </section>
  </section>
</template>

<style scoped>
.goal-constraints{margin:12px 0;color:#70816c;font-size:12px}.goal-constraints summary{cursor:pointer;margin-bottom:10px}.roadmap{margin-top:24px;border-top:1px solid #e2e9dd;padding-top:18px}.roadmap h3{font-size:15px;margin:22px 0 10px}.roadmap li{font-size:13px;line-height:1.9;margin:7px 0}.knowledge-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:16px 0}.knowledge-card{border:1px solid #dde6d7;border-radius:10px;background:#f7f9f3;padding:16px;min-width:0}.knowledge-card input{width:100%}.knowledge-card p{font-size:12px;line-height:1.9;color:#63775e;overflow-wrap:anywhere}.knowledge-card button.secondary{margin-top:13px}.remove-point{margin-left:auto;background:transparent;color:#8a655a;font-size:11px}.clarification-box{background:#f2f5e9;border-radius:8px;padding:14px;margin:16px 0}.clarification-box p{font-size:12px;color:#758368}.clarification-box button{margin-top:8px}@media(max-width:700px){.knowledge-grid{grid-template-columns:1fr}}
</style>
