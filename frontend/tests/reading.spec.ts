import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
const doc = { id: 'reading-fixture', title: 'Tool selection study', kind: 'pdf', page_count: 2, warnings: [], document_summary: null }
const summary = { abstract: '本文研究了工具选择方法，实验完成了一百个任务中的八十个，并指出英语任务之外的泛化仍需验证。', model: 'fixture-model', created_at: '2026-10-23T00:00:00Z', scope: 'fulltext', evidence: [{ page: 2, quote: 'The model completed 80 of 100 tasks.' }] }

test('downloads summary, question and comparison without generating again', async ({ page }) => {
  const posts: string[] = []
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()) })
  await page.route('**/api/readings/reading-fixture', route => route.fulfill({ json: { ...doc, document_summary: summary } }))
  await page.route('**/api/readings/reading-fixture/questions', route => route.fulfill({ json: [{
    id: 'q-export', question: '有什么局限？', supported: true, reason: '', retrieval: 'keyword', created_at: '2026-10-25',
    claims: [{ text: '仅英语任务', evidence: [{ id: 'c2', page: 2, section: 'Limitations', text: 'Only English tasks were evaluated.' }] }],
  }] }))
  await page.route('**/api/comparisons', route => route.fulfill({ json: [{
    id: 'export-comparison', created_at: '2026-10-25', papers: [{ id: doc.id, title: doc.title }, { id: 'other', title: 'Other paper' }], dimensions: ['method'], cells: [
      { reading_id: doc.id, dimension: 'method', supported: true, text: 'Method A', evidence: [{ id: 'e1', page: 2, text: 'Evidence for method A.' }] },
      { reading_id: 'other', dimension: 'method', supported: false, text: '未找到足够信息', evidence: [] },
    ],
  }] }))
  await page.goto('/')
  await page.getByLabel('最近摘要').selectOption(doc.id)
  async function exported(button: string) {
    const event = page.waitForEvent('download')
    await page.getByRole('button', { name: button, exact: true }).click()
    const download = await event
    expect(download.suggestedFilename()).toMatch(/\.md$/)
    return readFile((await download.path())!, 'utf-8')
  }
  expect(await exported('导出摘要 Markdown')).toContain('PDF 第 2 页')
  const answer = await exported('导出问答 Markdown')
  expect(answer).toContain('Only English tasks were evaluated')
  expect(answer).toContain(doc.title)
  await page.getByLabel('对比历史').selectOption('export-comparison')
  const comparison = await exported('导出对比 Markdown')
  expect(comparison).toContain('Other paper')
  expect(comparison).toContain('依据不足')
  expect(comparison).toContain('Evidence for method A')
  expect(posts).toEqual([])
})
test.beforeEach(async ({ page }) => {
  await page.route('http://paperscout.test/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.startsWith('/api/')) return route.fulfill({ status: 404, json: { detail: 'unmocked endpoint' } })
    const file = path === '/' ? 'index.html' : path.slice(1)
    if (file.includes('..')) return route.abort()
    await route.fulfill({ body: await readFile(resolve('dist', file)), contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' })
  })
  await page.route('https://fonts.googleapis.com/**', route => route.abort())
  await page.route('**/api/health', route => route.fulfill({ json: { model_configured: true, model: 'fixture-model' } }))
  await page.route('**/api/history', route => route.fulfill({ json: [] }))
  await page.route('**/api/comparisons', route => route.fulfill({ json: [] }))
  await page.route('**/api/agent/runs', route => route.fulfill({ json: [] }))
  await page.route('**/api/readings/*/questions', route => route.fulfill({ json: [] }))
  await page.route('**/api/readings', route => route.fulfill({ json: [doc] }))
  await page.route('**/api/readings/reading-fixture', route => route.fulfill({ json: doc }))
})


test('upload automatically generates one full abstract and restores it', async ({ page }) => {
  await page.route('**/api/readings/upload?*', route => route.fulfill({ json: doc }))
  await page.route('**/api/readings/reading-fixture/summary', route => route.fulfill({ json: summary }))
  await page.goto('/')
  await page.getByLabel('上传 PDF').setInputFiles({ name: 'fixture.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-fixture') })
  await expect(page.getByText(summary.abstract, { exact: true })).toBeVisible()
  await expect(page.getByLabel('章节 / 文本段')).toHaveCount(0)
  await expect(page.getByText('可核对原文', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重新生成摘要' })).toBeEnabled()
  await page.route('**/api/readings/reading-fixture', route => route.fulfill({ json: { ...doc, document_summary: summary } }))
  await page.reload()
  await page.getByLabel('最近摘要').selectOption(doc.id)
  await expect(page.getByText(summary.abstract, { exact: true })).toBeVisible()
  await page.locator('.summary-panel').screenshot({ path: 'test-results/full-summary-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.locator('.summary-panel').screenshot({ path: 'test-results/full-summary-mobile.png' })
})

test('failed auto summary retains uploaded file and offers retry', async ({ page }) => {
  await page.route('**/api/readings/upload?*', route => route.fulfill({ json: doc }))
  await page.route('**/api/readings/reading-fixture/summary', route => route.fulfill({ status: 502, json: { detail: '全文摘要生成失败' } }))
  await page.goto('/')
  await page.getByLabel('上传 PDF').setInputFiles({ name: 'fixture.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-fixture') })
  await expect(page.getByRole('alert')).toContainText('全文摘要生成失败')
  await expect(page.getByRole('heading', { name: doc.title })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成全文摘要', exact: true })).toBeEnabled()
})

test('old chapter summaries are not shown as full abstracts', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ json: { model_configured: false } }))
  await page.route('**/api/readings/reading-fixture', route => route.fulfill({ json: { ...doc, summaries: [{ points: [{ text: 'old chapter summary' }] }] } }))
  await page.goto('/')
  await page.getByLabel('最近摘要').selectOption(doc.id)
  await expect(page.getByText('old chapter summary')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '生成全文摘要', exact: true })).toBeDisabled()
})


test('paper questions cite evidence, restore history and isolate selected papers', async ({ page }) => {
  let saved: any[] = []
  const answer = { id: 'q1', question: '这篇论文有哪些局限？', supported: true, reason: '', retrieval: 'keyword', created_at: '2026-10-24T00:00:00Z',
    claims: [{ text: '实验只覆盖英语任务。', evidence: [{ id: 'c2', page: 2, section: 'Limitations', text: 'Only English tasks were evaluated.' }] }] }
  await page.route('**/api/readings/reading-fixture/questions', async route => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({ question: answer.question })
      saved = [answer]
      await route.fulfill({ json: answer })
    } else await route.fulfill({ json: saved })
  })
  await page.goto('/')
  await page.getByLabel('最近摘要').selectOption(doc.id)
  await page.getByLabel('论文问题').fill(answer.question)
  await expect(page.getByLabel('检索范围')).toHaveCount(0)
  await page.getByRole('button', { name: '提问', exact: true }).click()
  await expect(page.getByText('实验只覆盖英语任务。', { exact: true })).toBeVisible()
  await page.getByText('查看回答依据', { exact: true }).click()
  await expect(page.getByText('PDF 第 2 页 · Limitations')).toBeVisible()
  await expect(page.getByText('Only English tasks were evaluated.')).toBeVisible()
  await page.locator('.paper-questions').screenshot({ path: 'test-results/qa-desktop.png' })
  await page.reload()
  await page.getByLabel('最近摘要').selectOption(doc.id)
  await expect(page.getByText('实验只覆盖英语任务。', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.locator('.paper-questions').screenshot({ path: 'test-results/qa-mobile.png' })
  await page.route('**/api/readings', route => route.fulfill({ json: [doc, { ...doc, id: 'other', title: 'Other paper' }] }))
  await page.route('**/api/readings/other', route => route.fulfill({ json: { ...doc, id: 'other', title: 'Other paper' } }))
  await page.reload()
  await page.getByLabel('最近摘要').selectOption('other')
  await expect(page.getByText('实验只覆盖英语任务。', { exact: true })).toHaveCount(0)
})

test('paper question insufficient evidence and cancellation', async ({ page }) => {
  await page.route('**/api/readings/reading-fixture/questions', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] })
    return route.fulfill({ json: { id: 'absent', question: 'cost?', supported: false, reason: '当前片段没有报告成本。', claims: [], retrieval: 'keyword', created_at: '2026-10-24' } })
  })
  await page.goto('/')
  await page.getByLabel('最近摘要').selectOption(doc.id)
  await page.getByLabel('论文问题').fill('cost?')
  await page.getByRole('button', { name: '提问', exact: true }).click()
  await expect(page.getByText('依据不足', { exact: true })).toBeVisible()
  await expect(page.getByText('当前片段没有报告成本。')).toBeVisible()
  await page.route('**/api/readings/reading-fixture/questions', async route => {
    await new Promise(resolve => setTimeout(resolve, 1000)); await route.abort()
  })
  await page.getByLabel('论文问题').fill('methods?')
  await page.getByRole('button', { name: '提问', exact: true }).click()
  await page.getByRole('button', { name: '取消问答', exact: true }).click()
  await expect(page.getByText('已取消本次问答。')).toBeVisible()
})

test('compare PDFs with evidence and restore table on mobile', async ({ page }) => {
  let saved: any[] = []
  const other = { ...doc, id: 'other', title: 'Another study' }
  await page.route('**/api/readings', route => route.fulfill({ json: [doc, other, { ...doc, id: 'abstract', title: 'Abstract only', kind: 'abstract' }] }))
  await page.route('**/api/comparisons', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: saved })
    const body = route.request().postDataJSON()
    expect(body.reading_ids).toEqual([doc.id, other.id])
    const answer = { id: 'comparison-1', created_at: '2026-10-24T00:00:00Z', papers: [doc, other], dimensions: body.dimensions,
      cells: [doc, other].flatMap(p => body.dimensions.map((dimension: string) => ({ reading_id: p.id, dimension,
        supported: dimension !== 'limitations', text: dimension === 'limitations' ? '未找到相关信息' : '使用语言模型完成任务',
        evidence: dimension === 'limitations' ? [] : [{ id: p.id + ':c1', page: 2, text: 'The model selects tools.' }] }))) }
    saved = [answer]; return route.fulfill({ json: answer })
  })
  await page.goto('/')
  const panel = page.locator('.comparison')
  await expect(panel.getByRole('button', { name: '生成对比', exact: true })).toBeDisabled()
  await panel.getByLabel(doc.title, { exact: true }).check()
  await panel.getByLabel(other.title, { exact: true }).check()
  await expect(panel.getByLabel('Abstract only', { exact: true })).toHaveCount(0)
  await panel.getByRole('button', { name: '生成对比', exact: true }).click()
  await expect(panel.getByRole('table')).toBeVisible()
  await expect(panel.getByText('未找到相关信息', { exact: true })).toHaveCount(2)
  await panel.getByText('查看对比依据', { exact: true }).first().click()
  await expect(panel.getByText('The model selects tools.').first()).toBeVisible()
  await page.reload()
  await expect(panel.getByLabel('对比历史')).toHaveValue('')
  await expect(panel.getByRole('table')).toHaveCount(0)
  await panel.getByLabel('对比历史').selectOption('comparison-1')
  await expect(panel.getByRole('table')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await panel.screenshot({ path: 'test-results/comparison-mobile.png' })
})

test('comparison can be cancelled or fail without losing prior result', async ({ page }) => {
  await page.route('**/api/readings', route => route.fulfill({ json: [doc, { ...doc, id: 'other', title: 'Another study' }] }))
  await page.goto('/')
  const panel = page.locator('.comparison')
  await panel.getByLabel(doc.title, { exact: true }).check()
  await panel.getByLabel('Another study', { exact: true }).check()
  await page.route('**/api/comparisons', route => route.fulfill({ status: 502, json: { detail: '对比证据校验失败' } }))
  await panel.getByRole('button', { name: '生成对比', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('对比证据校验失败')
  await page.route('**/api/comparisons', async route => { await new Promise(resolve => setTimeout(resolve, 1000)); await route.abort() })
  await panel.getByRole('button', { name: '生成对比', exact: true }).click()
  await panel.getByRole('button', { name: '取消对比', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('已取消对比')
})
