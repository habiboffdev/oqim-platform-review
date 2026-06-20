import { expect, type Page } from '@playwright/test'

export type SmokeCredentials = {
  phone: string
  password: string
}

export function smokeCredentialsFromEnv(prefix = 'OQIM_SMOKE'): SmokeCredentials | null {
  const phone = process.env[`${prefix}_PHONE`]
  const password = process.env[`${prefix}_PASSWORD`]
  if (!phone || !password) return null
  return { phone, password }
}

export async function authenticateSeededSession(page: Page, credentials: SmokeCredentials) {
  const login = await page.request.post('/api/auth/login', {
    data: {
      phone_number: credentials.phone,
      password: credentials.password,
    },
  })
  const loginBody = await login.text()
  expect(
    login.ok(),
    `/api/auth/login should authenticate seeded smoke user: ${login.status()} ${loginBody}`,
  ).toBeTruthy()

  const me = await page.request.get('/api/auth/me')
  const meBody = await me.text()
  expect(me.ok(), `/api/auth/me should read seeded smoke user: ${me.status()} ${meBody}`).toBeTruthy()

  const session = await page.request.get('/api/auth/session')
  const sessionBody = await session.text()
  expect(
    session.ok(),
    `/api/auth/session should expose durable seeded session projection: ${session.status()} ${sessionBody}`,
  ).toBeTruthy()
  expect(sessionBody, 'durable auth/session projection must be DB-backed for smoke').toContain(
    'auth_session_projection.v1',
  )
}

export async function openProtectedRoute(page: Page, route: string) {
  await page.goto(route)
  await expect(page).not.toHaveURL(/\/login/)
  await expect(page.getByText(/tarmoq xatosi/i)).toHaveCount(0)
  await expect(page.getByText(/xizmat vaqtincha ishlamayapti/i)).toHaveCount(0)
}
