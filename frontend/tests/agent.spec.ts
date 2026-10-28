import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
const emptyRun = { id: 'job1', goal: '比较论文方法', status: 'running', message: '正在执行', created_at: '2026-10-24',
  events: [], papers: [], readings: [], comparison: null, answers: [] }

test('local routing selection pauses for clarification and resumes the same task', async ({ page }) => {
  let current: any = { ...emptyRun, router_mode: 'local', status: 'needs_input', clarification: '你选择哪两篇？' }
  await page.route('**/api/agent/runs', route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] })
    expect(route.request().postDataJSON().router_mode).toBe('local')
    return route.fulfill({ status: 202, json: current })
  })
  await page.route('**/api/agent/runs/job1/reply', route => {
    expect(route.request().postDataJSON()).toEqual({ text: '选择第一篇与第二篇' })
    current = { ...current, status: 'completed', clarification: null, message: '对比完成' }
    return route.fulfill({ status: 202, json: current })
  })
  await page.goto('/')
  const panel = page.locator('.research-agent')
  await expect(panel.getByLabel('工具路由')).toHaveValue('cloud')
  await panel.getByLabel('工具路由').selectOption('local')
  await panel.getByLabel('研究任务', { exact: true }).fill('比较论文方法')
  await panel.getByRole('button', { name: '启动研究任务' }).click()
  await expect(panel.getByRole('status')).toContainText('等待补充信息')
  await panel.getByLabel('补充信息').fill('选择第一篇与第二篇')
  await panel.getByRole('button', { name: '补充并继续' }).click()
  await expect(panel.getByRole('status')).toContainText('本地微调路由')
  await expect(panel.getByRole('status')).toContainText('对比完成')
  await panel.getByLabel('执行模式').selectOption('multi')
  await expect(panel.getByLabel('工具路由')).toHaveCount(0)
  await panel.getByLabel('执行模式').selectOption('single')
  await expect(panel.getByLabel('工具路由')).toHaveValue('cloud')
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
  await page.route('**/api/health', route => route.fulfill({ json: { model_configured: true } }))
  await page.route('**/api/history', route => route.fulfill({ json: [] }))
  await page.route('**/api/comparisons', route => route.fulfill({ json: [] }))
  await page.route('**/api/readings', route => route.fulfill({ json: [{ id: 'p1', title: 'Paper One', kind: 'pdf' }, { id: 'p2', title: 'Paper Two', kind: 'pdf' }] }))
})

test('agent starts with selected PDFs, polls evidence results and restores history', async ({ page }) => {
  let saved: any[] = []
  const finished = { ...emptyRun, status: 'completed', message: '已结束', events: [{ tool: 'compare', status: 'ok', message: '已生成对比' }],
    comparison: { papers: [{ id: 'p1', title: 'Paper One' }, { id: 'p2', title: 'Paper Two' }], dimensions: ['method'], cells: [
      { reading_id: 'p1', dimension: 'method', text: '工具选择方法', supported: true, evidence: [{ id: 'p1:c1', page: 1, text: 'Tools are selected using a model.' }] },
      { reading_id: 'p2', dimension: 'method', text: '未找到足够信息', supported: false, evidence: [] }] } }
  await page.route('**/api/agent/runs', route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: saved })
    expect(route.request().postDataJSON()).toEqual({ goal: '比较论文方法', reading_ids: ['p1', 'p2'], mode: 'single', router_mode: 'cloud' })
    saved = [emptyRun]; return route.fulfill({ status: 202, json: emptyRun })
  })
  await page.route('**/api/agent/runs/job1', route => { saved = [finished]; return route.fulfill({ json: finished }) })
  await page.goto('/')
  const panel = page.locator('.research-agent')
  await panel.getByLabel('研究任务', { exact: true }).fill('比较论文方法')
  await panel.getByText('使用已上传的 PDF（可选，最多 5 篇）').click()
  await panel.getByLabel('Paper One', { exact: true }).check()
  await panel.getByLabel('Paper Two', { exact: true }).check()
  await panel.getByRole('button', { name: '启动研究任务' }).click()
  await expect(panel.getByRole('table')).toBeVisible()
  await panel.getByText('查看对比依据').click()
  await expect(panel.getByText('Tools are selected using a model.')).toBeVisible()
  await page.reload()
  await expect(panel.getByLabel('研究任务历史')).toHaveValue('')
  await expect(panel.locator('.agent-result')).toHaveCount(0)
  await panel.getByLabel('研究任务历史').selectOption('job1')
  await expect(panel.getByText('工具选择方法', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await panel.screenshot({ path: 'test-results/agent-mobile.png' })
})

test('running task survives page reload and supports cancellation', async ({ page }) => {
  await page.route('**/api/agent/runs', route => route.fulfill({ json: [emptyRun] }))
  await page.route('**/api/agent/runs/job1', route => route.fulfill({ json: emptyRun }))
  await page.route('**/api/agent/runs/job1/cancel', route => route.fulfill({ json: { ...emptyRun, status: 'cancelled', message: '任务已取消' } }))
  await page.goto('/')
  const panel = page.locator('.research-agent')
  await expect(panel.getByLabel('研究任务历史')).toHaveValue('')
  await expect(panel.locator('.agent-result')).toHaveCount(0)
  await panel.getByLabel('研究任务历史').selectOption('job1')
  await expect(panel.getByRole('button', { name: '取消研究任务' })).toBeVisible()
  await panel.getByRole('button', { name: '取消研究任务' }).click()
  await expect(panel.getByRole('status')).toContainText('已取消')
  await expect(panel.getByRole('button', { name: '取消研究任务' })).toHaveCount(0)
})

test('agent start errors are actionable', async ({ page }) => {
  await page.route('**/api/agent/runs', route => route.request().method() === 'GET'
    ? route.fulfill({ json: [] }) : route.fulfill({ status: 409, json: { detail: '已有研究任务运行中，请等待完成或先取消。' } }))
  await page.goto('/')
  const panel = page.locator('.research-agent')
  await panel.getByLabel('研究任务', { exact: true }).fill('寻找论文')
  await panel.getByRole('button', { name: '启动研究任务' }).click()
  await expect(panel.getByRole('alert')).toContainText('已有研究任务运行中')
})

test('multi-agent selection persists and role events remain visible in history', async ({ page }) => {
  const active = { ...emptyRun, mode: 'multi', events: [
    { role: 'coordinator', tool: 'delegate', status: 'running', message: '分配论文检索任务' },
    { role: 'retrieval', tool: 'search', status: 'ok', message: '已找到候选论文' },
    { role: 'analysis', tool: 'answer', status: 'ok', message: '已核对原文依据' }] }
  const finished = { ...active, status: 'completed', message: '已结束' }
  let saved: any[] = []
  await page.route('**/api/agent/runs', route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: saved })
    expect(route.request().postDataJSON()).toEqual({ goal: '比较论文方法', reading_ids: [], mode: 'multi', router_mode: 'cloud' })
    saved = [active]
    return route.fulfill({ status: 202, json: active })
  })
  await page.route('**/api/agent/runs/job1', route => {
    saved = [finished]
    return route.fulfill({ json: finished })
  })
  await page.goto('/')
  const panel = page.locator('.research-agent')
  await expect(panel.getByLabel('执行模式')).toHaveValue('single')
  await panel.getByLabel('执行模式').selectOption('multi')
  await panel.getByLabel('研究任务', { exact: true }).fill('比较论文方法')
  await panel.getByRole('button', { name: '启动研究任务' }).click()
  await expect(panel.getByLabel('执行模式')).toBeDisabled()
  await expect(panel.getByRole('status')).toContainText('多 Agent')
  await expect(panel.locator('li').filter({ hasText: '协调 Agent' })).toContainText('分配论文检索任务')
  await expect(panel.locator('li').filter({ hasText: '检索 Agent' })).toContainText('已找到候选论文')
  await expect(panel.locator('li').filter({ hasText: '分析 Agent' })).toContainText('已核对原文依据')
  await expect(panel.getByRole('status')).toContainText('已结束')
  await page.reload()
  await expect(panel.locator('.agent-result')).toHaveCount(0)
  await panel.getByLabel('研究任务历史').selectOption('job1')
  await expect(panel.getByRole('status')).toContainText('多 Agent')
  await expect(panel.getByLabel('执行模式')).toHaveValue('single') // New tasks still default to single.
})
