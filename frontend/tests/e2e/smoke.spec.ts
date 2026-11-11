import { expect, test } from '@playwright/test'

// SPA shell smoke test — intentionally credential-free.
//
// playwright.config.ts points at a locally built SPA served by `vite preview`.
// These assertions only require the app shell to boot, so they stay green
// without a live backend, a database, or any user account. Deeper flows
// (login → chat → scheduled tasks) belong in integration specs, not here.
//
// Run: npm run test:e2e   (webServer in playwright.config.ts builds + serves)
test.describe('PrismClaw SPA shell', () => {
  test('renders the login form without live credentials', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error.message))

    await page.goto('/')

    await expect(page.locator('#root')).not.toBeEmpty()
    await expect(page.locator('h2')).toHaveText('欢迎使用')
    await expect(page.getByPlaceholder('请输入昵称或邮箱')).toBeVisible()
    await expect(page.getByPlaceholder('请输入密码')).toBeVisible()
    await expect(page.locator('button[type="submit"]')).toContainText('登录')
    await expect(page.getByText('页面异常')).toHaveCount(0)

    expect(pageErrors).toEqual([])
  })

  test('switches between login, register and reset views', async ({ page }) => {
    await page.goto('/')

    await page.getByRole('button', { name: '新用户注册' }).click()
    await expect(page.locator('h2')).toHaveText('创建账号')

    await page.getByRole('button', { name: '返回登录' }).click()
    await expect(page.locator('h2')).toHaveText('欢迎使用')

    await page.getByRole('button', { name: '忘记密码？' }).click()
    await expect(page.locator('h2')).toHaveText('重置密码')

    await page.getByRole('button', { name: '返回登录' }).click()
    await expect(page.locator('h2')).toHaveText('欢迎使用')
  })

  test('login button stays disabled until an account is entered', async ({ page }) => {
    await page.goto('/')

    const submit = page.locator('button[type="submit"]')
    await expect(submit).toBeDisabled()
    await page.getByPlaceholder('请输入昵称或邮箱').fill('smoke-user')
    await expect(submit).toBeEnabled()
  })
})
