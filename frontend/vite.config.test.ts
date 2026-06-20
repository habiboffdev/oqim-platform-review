// @vitest-environment node

import { describe, expect, it } from 'vitest'

import config from './vite.config'

describe('vite dev proxy', () => {
  it('binds the dev server to IPv4 loopback compatible host', () => {
    expect(config.server?.host).toBe('0.0.0.0')
    expect(config.server?.port).toBe(4200)
    expect(config.server?.strictPort).toBe(true)
  })

  it('does not expose the retired Telegram Web K proxy', () => {
    const proxy = config.server?.proxy ?? {}

    expect(Object.keys(proxy).sort()).toEqual(['/api'])
    expect(proxy).not.toHaveProperty('/tg')
  })
})
