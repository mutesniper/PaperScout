<script setup lang="ts">
import { ref } from 'vue'
const dialog = ref<HTMLDialogElement | null>(null)
</script>

<template>
  <button type="button" class="guide-trigger" @click="dialog?.showModal()">使用指引 <span aria-hidden="true">↗</span></button>
  <dialog ref="dialog" class="usage-dialog" aria-labelledby="usage-title" @click="($event.target === dialog) && dialog?.close()">
    <header class="usage-header"><div><div class="eyebrow">YOUR RESEARCH COMPANION</div><h2 id="usage-title">使用指引</h2></div><button type="button" class="close-guide" aria-label="关闭使用指引" @click="dialog?.close()">×</button></header>
    <p class="usage-intro">从发现论文，到得到有依据的答案。</p>
    <div class="usage-grid">
      <section><span class="step">01</span><h3>发现论文</h3><p>直接输入简短英文关键词，最多 3 行，每行独立查询后汇总。也可以描述想法，让模型生成可修改的检索计划。</p><p>每个来源最多获取 100 篇候选，合并去重后排序。时间和引用量排序仅针对本次候选，未知引用量排在最后。公开链接不保证下载成功。</p></section>
      <section><span class="step">02</span><h3>交给研究助手</h3><p>说明目标，助手会按需搜索、下载、问答或对比。也可选最多 5 篇已上传的 PDF，直接分析。</p><p>单 Agent 适合直接任务；多 Agent 由协调、检索、分析角色按需串行协作，可能增加调用与耗时。任务最多运行 10 分钟，可取消；刷新后从历史恢复查看。</p></section>
      <section><span class="step">03</span><h3>摘要与问答</h3><p>支持 20 MB / 100 页以内的文字型 PDF。配置模型后，上传会自动生成全文摘要，长论文可能多次调用。来源摘要概括不代表已阅读全文。</p><p>选择论文后即可提问。每个问题独立从全文检索；关键词检索建议沿用论文术语，语义检索更适合跨语言提问。</p></section>
      <section><span class="step">04</span><h3>对比与导出</h3><p>选择 2～5 篇 PDF 和对比维度即可生成对比，无需先生成摘要。展开依据查看原文与页码。</p><p>摘要、问答和对比支持 Markdown 导出；搜索结果可生成引用。历史结果需要主动选择，不会默认展开。</p></section>
    </div>
    <details class="usage-details"><summary>模型调用与结果核验</summary><p>摘要会将提取文本发送到配置的模型服务；问答和对比发送相关片段。首次语义检索会将论文文本分批发送给 Embedding 服务建立索引，后续复用。研究助手可能多次调用模型。</p><p>引用可追溯不代表推论必然正确，检索未命中也不等于论文没有相关内容。不同实验条件下的指标不能直接判断优劣，重要结论请核对原文。</p></details>
  </dialog>
</template>
