import { test, expect } from '@playwright/test'
import { answerMarkdown, comparisonMarkdown, summaryMarkdown } from '../src/markdown'

test('exports keep evidence, refusal and source-only scope without active markup', () => {
  const paper = { id: 'p', title: '<script>alert(1)</script>|paper', source_url: 'javascript:alert(1)' }
  const text = answerMarkdown(paper, { question: 'Q?', supported: false, reason: '没有依据', created_at: 'today', claims: [] })
  expect(text).toContain('依据不足')
  expect(text).not.toContain('<script>')
  expect(text).not.toContain('javascript:')
  const summary = summaryMarkdown(paper, { abstract: 'abstract', scope: 'abstract', model: 'm', created_at: 'today', evidence: [{ page: 1, quote: 'source' }] })
  expect(summary).toContain('来源摘要（未读取全文）')
  expect(summary).not.toContain('PDF 第 1 页')
  const comparison = comparisonMarkdown({ created_at: 'today', papers: [paper], dimensions: ['method'], cells: [
    { reading_id: 'p', dimension: 'method', supported: true, text: 'a|b\nnext', evidence: [{ page: 3, text: 'original evidence' }] },
  ] })
  expect(comparison).toContain('a\\|b<br>next')
  expect(comparison).toContain('PDF 第 3 页')
  expect(comparison).toContain('original evidence')
})
