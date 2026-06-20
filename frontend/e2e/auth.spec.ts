import { test, expect } from '@playwright/test'

test.describe('Authentication flow', () => {
  test('redirects unauthenticated users to login page', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/.*login/)
  })

  test('login page renders with phone input and submit button', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByLabel(/telefon raqam/i)).toBeVisible()
    await expect(page.getByLabel(/parol/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /kirish/i })).toBeVisible()
  })

  test('login page shows validation on empty submit', async ({ page }) => {
    await page.goto('/login')
    await page.locator('button[type="submit"]').click()
    // Should stay on login page — no navigation
    await expect(page).toHaveURL(/.*login/)
  })

  test('register page is accessible', async ({ page }) => {
    await page.goto('/register')
    await expect(page.getByLabel(/^ism$/i)).toBeVisible()
    await expect(page.getByLabel(/telefon raqam/i)).toBeVisible()
    await expect(page.getByLabel(/parol/i)).toBeVisible()
  })
})
