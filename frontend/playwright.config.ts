import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://paperscout.test', channel: 'chrome', headless: true },
})
