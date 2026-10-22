import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

test('goal clarification, editable knowledge and contextual search', async ({ page }) => {
  await page.route('http://paperscout.test/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.startsWith('/api/')) return route.fulfill({ status: 404, json: {} })
    const file = path === '/' ? 'index.html' : path.slice(1)
    await route.fulfill({ body: await readFile(resolve('dist', file)), contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' })
  })
  await page.route('https://fonts.googleapis.com/**', route => route.abort())
  await page.route('**/api/health', route => route.fulfill({ json: { model_configured: true, model: 'test-model' } }))
  await page.route('**/api/history', route => route.fulfill({ json: [] }))
  let calls = 0
  await page.route('**/api/analyze', async route => {
    calls++
    if (calls === 2) expect(route.request().postDataJSON().clarification).toBe('只用本地小模型')
    await route.fulfill({ json: { model: 'test-model', analysis: {
      goal: '让小模型调用工具', assumptions: ['先尝试已有模型'], questions: calls === 1 ? ['是否本地部署？'] : [],
      knowledge: [{ name: '工具调用', importance: 'core', explanation: '选择工具和参数', goal_connection: '让小模型执行用户任务', plan: { queries: ['small model tool use'], year_from: null, year_to: null } }],
      steps: ['定义工具接口', '评估任务成功率'], alternatives: ['不足时再微调'] } } })
  })
  await page.route('**/api/search', async route => {
    const request = route.request().postDataJSON()
    expect(request.plan.queries).toEqual(['small language model function calling'])
    expect(request.context).toEqual({ goal: '让小模型调用工具', topic: '工具调用', reason: '让小模型执行用户任务' })
    await route.fulfill({ json: { id: 'test', created_at: '2026-10-23T00:00:00Z', request, papers: [], sources: [], candidate_count: 0 } })
  })
  await page.goto('/')
  await page.getByLabel('用自己的话描述目标').fill('我想用小模型自动使用工具')
  await page.getByRole('button', { name: '分析目标', exact: true }).click()
  await expect(page.getByText('是否本地部署？')).toBeVisible()
  await page.getByLabel('补充回答').fill('只用本地小模型')
  await page.getByRole('button', { name: '根据回答更新路线' }).click()
  await expect(page.getByText('是否本地部署？')).toHaveCount(0)
  await page.getByLabel('英文检索词').fill('small language model function calling')
  await page.getByRole('button', { name: '补充知识点' }).click()
  await expect(page.getByLabel('知识点名称')).toHaveCount(2)
  await page.getByRole('button', { name: '删除知识点 新知识点' }).click()
  await page.screenshot({ path: 'test-results/goal-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/goal-mobile.png', fullPage: true })
  await page.getByRole('button', { name: '查找相关论文' }).click()
  await expect(page.getByText('为“工具调用”找到的候选论文', { exact: false })).toBeVisible()
})
