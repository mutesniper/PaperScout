import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

// Deterministic UI fixtures only; application requests always use real providers.
test.beforeEach(async ({ page }) => {
  // Serve the real production build in-browser without opening a local socket.
  await page.route('http://paperscout.test/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.startsWith('/api/')) return route.fulfill({ status: 404, json: { detail: 'unmocked test endpoint' } })
    const file = path === '/' ? 'index.html' : path.slice(1)
    if (file.includes('..')) return route.abort()
    const contentType = file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'
    await route.fulfill({ body: await readFile(resolve('dist', file)), contentType })
  })
  await page.route('https://fonts.googleapis.com/**', route => route.abort())
  await page.route('**/api/health', route => route.fulfill({ json: { model_configured: false, model: null } }))
  await page.route('**/api/readings', route => route.fulfill({ json: [] }))
  await page.route('**/api/history', route => route.fulfill({ json: [] }))
})

test('manual search preserves successful source and labels access honestly', async ({ page }) => {
  await page.route('**/api/search', async route => {
    const request = route.request().postDataJSON()
    expect(request.plan.queries).toEqual(['tool calling'])
    expect(request.sort).toBe('relevance')
    expect(request).not.toHaveProperty('candidate_limit')
    expect(request.limit).toBe(10)
    await route.fulfill({ json: { id: 'fixture', created_at: '2026-10-22T00:00:00Z', request,
      sources: [{ source: 'arxiv', status: 'ok', count: 1 }, { source: 'semantic_scholar', status: 'error', count: 0, message: '来源限流' }],
      papers: [{ id: 'fixture-paper', title: 'Tool Calling Test Fixture', year: 2024, authors: ['Test Author'], sources: ['arxiv'], url: 'https://arxiv.org/abs/2401.00001', abstract: 'Fixture abstract', fulltext_url: null, access: 'abstract_only', evidence: '未取得公开全文链接，不能据此判断是否付费。' }] } })
  })
  await page.goto('/')
  await page.screenshot({ path: 'test-results/desktop.png', fullPage: true })
  await expect(page.getByRole('button', { name: '生成检索计划' })).toBeDisabled()
  await page.getByLabel('检索词', { exact: false }).fill('tool calling')
  await page.getByRole('button', { name: '开始探索' }).click()
  await expect(page.getByRole('heading', { name: 'Tool Calling Test Fixture' })).toBeVisible()
  await expect(page.getByText('来源限流', { exact: true })).toBeVisible()
  await page.getByText('查看来源摘要', { exact: true }).click()
  await expect(page.getByText('Fixture abstract', { exact: true })).toBeVisible()
  await page.getByLabel('仅显示有公开全文链接的论文').check()
  await expect(page.getByText('没有符合当前条件的论文')).toBeVisible()
})

test('sort controls are sent and restored from history', async ({ page }) => {
  let saved: any
  await page.route('**/api/search', async route => {
    const request = route.request().postDataJSON()
    expect(request.sort).toBe('citations')
    expect(request).not.toHaveProperty('candidate_limit')
    expect(request.limit).toBe(5)
    saved = { id: 'sorted', created_at: '2026-10-22T00:00:00Z', request, papers: [], sources: [], candidate_count: 0, ranking_note: '本次候选论文排序', warnings: [] }
    await route.fulfill({ json: saved })
  })
  await page.route('**/api/history', route => route.fulfill({ json: saved ? [{ id: 'sorted', created_at: saved.created_at, queries: ['tools'], count: 0 }] : [] }))
  await page.route('**/api/history/sorted', route => route.fulfill({ json: saved }))
  await page.goto('/')
  await page.getByLabel('检索词', { exact: false }).fill('tools')
  await page.getByLabel('排序方式').selectOption('citations')
  await page.getByLabel('返回前多少篇').selectOption('5')
  await page.getByRole('button', { name: '开始探索' }).click()
  await expect(page.getByText('本次候选论文排序', { exact: true })).toBeVisible()
  await page.getByLabel('排序方式').selectOption('oldest')
  await page.getByRole('button', { name: /tools/ }).click()
  await expect(page.getByLabel('排序方式')).toHaveValue('citations')
  await expect(page.getByLabel('返回前多少篇')).toHaveValue('5')
})

test('invalid years do not start a request', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('检索词', { exact: false }).fill('tool calling')
  await page.getByLabel('起始年份').fill('2025')
  await page.getByLabel('结束年份').fill('2020')
  await page.getByRole('button', { name: '开始探索' }).click()
  await expect(page.getByRole('alert')).toContainText('有效年份')
})

test('mobile viewport fits and a pending request can be cancelled', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/search', async route => { await new Promise(resolve => setTimeout(resolve, 1500)); await route.abort() })
  await page.goto('/')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/mobile.png', fullPage: true })
  await page.getByLabel('检索词', { exact: false }).fill('tool calling')
  await page.getByRole('button', { name: '开始探索' }).click()
  await page.getByRole('button', { name: '取消请求' }).click()
  await expect(page.getByText('已取消本次请求。')).toBeVisible()
  await expect(page.getByRole('button', { name: '开始探索' })).toBeEnabled()
})


test('citation generation, format switch and clipboard fallback', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('denied') } } })
  })
  await page.route('**/api/search', route => route.fulfill({ json: {
    id: 'citation-test', created_at: '2026-10-24', request: route.request().postDataJSON(), sources: [], papers: [{
      id: 'arxiv:2505.08588', title: 'Tools & Models: A Test', authors: ['Alice Smith', 'Bob Chen', 'Carol Jones', 'Dan Lee'], year: 2025,
      arxiv_id: '2505.08588', sources: ['arxiv'], url: 'https://arxiv.org/abs/2505.08588', doi: '10/published-version',
      abstract: '', access: 'open_preprint', evidence: 'test', fulltext_url: null,
    }],
  } }))
  await page.goto('/')
  await page.getByLabel('检索词', { exact: false }).fill('tools')
  await page.getByRole('button', { name: '开始探索' }).click()
  await page.getByRole('button', { name: '生成引用', exact: true }).click()
  const entry = page.getByLabel('参考文献条目')
  expect(await entry.inputValue()).toContain('Alice Smith, Bob Chen, Carol Jones, et al.')
  expect(await entry.inputValue()).toContain('https://arxiv.org/abs/2505.08588')
  expect(await entry.inputValue()).not.toContain('10/published-version')
  await page.getByLabel('引用格式').selectOption('bibtex')
  expect(await entry.inputValue()).toContain('@misc{')
  expect(await entry.inputValue()).toContain('archivePrefix = {arXiv}')
  expect(await entry.inputValue()).toContain('Dan Lee')
  expect(await entry.inputValue()).toContain('Tools \\& Models')
  await page.getByRole('button', { name: '复制引用', exact: true }).click()
  await expect(page.getByText(/浏览器未允许自动复制/)).toBeVisible()
  expect(await entry.evaluate((el: HTMLTextAreaElement) => el.selectionEnd - el.selectionStart)).toBe((await entry.inputValue()).length)
  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { (window as any).copiedCitation = text } } }) })
  await entry.fill('Edited citation')
  await page.getByRole('button', { name: '复制引用', exact: true }).click()
  await expect(page.getByText('已复制', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as any).copiedCitation)).toBe('Edited citation')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.locator('.citation-widget').screenshot({ path: 'test-results/citation-mobile.png' })
})
