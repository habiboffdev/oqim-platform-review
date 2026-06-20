import { expect, test } from '@playwright/test'
import {
  clearBrowserRuntimeState,
  readBrowserRuntimeState,
  seedStaleBrowserRuntimeState,
  staleBrowserMarker,
} from './helpers/browser-runtime-state'

test.describe('runtime-zero browser cache proof', () => {
  test('clears stale origin state before app reload', async ({ page }) => {
    await page.goto('/login')

    await seedStaleBrowserRuntimeState(page)
    await expect.poll(async () => readBrowserRuntimeState(page)).toMatchObject({
      localValue: staleBrowserMarker(),
      sessionValue: staleBrowserMarker(),
      hasCache: true,
      hasIndexedDb: true,
    })

    await clearBrowserRuntimeState(page)
    const cleared = await readBrowserRuntimeState(page)

    expect(cleared).toMatchObject({
      localValue: null,
      sessionValue: null,
      hasCache: false,
      hasIndexedDb: false,
      serviceWorkerCount: 0,
    })

    await page.reload()
    await expect(page.getByText(staleBrowserMarker())).toHaveCount(0)
  })
})
