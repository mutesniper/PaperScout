<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { makeCitation, type CitationPaper } from './citations'
const props = defineProps<{ paper: CitationPaper }>()
const opened = ref(false), format = ref<'text' | 'bibtex'>('text'), text = ref(''), status = ref('')
const area = ref<HTMLTextAreaElement | null>(null)
const today = new Date()
const accessed = ref(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`)
const missing = computed(() => [!props.paper.authors.length && '作者', !props.paper.year && '年份'].filter(Boolean).join('、'))
function generate() { text.value = makeCitation(props.paper, format.value, accessed.value); status.value = '' }
async function open() { opened.value = !opened.value; if (opened.value) { generate(); await nextTick(); area.value?.focus() } }
async function copy() {
  try { await navigator.clipboard.writeText(text.value); status.value = '已复制' }
  catch { area.value?.focus(); area.value?.select(); status.value = '浏览器未允许自动复制，已选中文本，请按 Ctrl+C（Mac：⌘C）。' }
}
</script>
<template>
  <div class="citation-widget">
    <button class="secondary" :aria-expanded="opened" @click="open">{{ opened ? '收起引用' : '生成引用' }}</button>
    <div v-if="opened" class="citation-panel">
      <div class="citation-options"><label>引用格式<select v-model="format" @change="generate"><option value="text">GB/T 7714—2015（在线资源参考格式）</option><option value="bibtex">BibTeX</option></select></label><label>引用日期<input v-model="accessed" type="date" required @change="generate"></label></div>
      <p>根据当前来源元数据生成；不补写未知的期刊、卷期和页码。作者姓名保留来源写法，请按学校或期刊要求核对；引用序号由你的论文统一编排。</p>
      <p v-if="missing" class="missing">来源缺少{{ missing }}，请补全后使用。</p>
      <label>参考文献条目<textarea ref="area" v-model="text" rows="6" spellcheck="false" /></label>
      <div class="copy-row"><button class="primary" :disabled="!text.trim()" @click="copy">复制引用</button><span role="status">{{ status }}</span></div>
    </div>
  </div>
</template>
<style scoped>
.citation-widget{margin-top:16px}.citation-panel{margin-top:12px;padding:18px;background:#f6f8f2;border:1px solid #dce3dd;border-radius:10px}.citation-options{display:flex;gap:16px;flex-wrap:wrap}.citation-options label{display:flex;flex-direction:column;gap:8px;max-width:100%}.citation-options select{max-width:100%}.citation-panel p{font-size:12px;line-height:1.8;color:#728168}.citation-panel .missing{color:#a25231}.citation-panel textarea{margin-top:8px;font-size:14px;line-height:1.8;overflow-wrap:anywhere}.copy-row{display:flex;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap}.copy-row span{font-size:12px;line-height:1.7}
</style>
