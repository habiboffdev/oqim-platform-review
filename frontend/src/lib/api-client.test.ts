// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { api, ApiError } from './api-client'

// Helper to mock fetch
function mockFetch(response: { status: number; ok: boolean; json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    status: response.status,
    ok: response.ok,
    json: response.json ?? (() => Promise.resolve({})),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('api-client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    // Reset document.cookie
    Object.defineProperty(document, 'cookie', {
      writable: true,
      value: '',
    })
  })

  describe('GET requests', () => {
    it('calls fetch with correct path and credentials', async () => {
      const fetchMock = mockFetch({ status: 200, ok: true, json: () => Promise.resolve({ id: 1 }) })

      const result = await api.get<{ id: number }>('/api/test')

      expect(fetchMock).toHaveBeenCalledWith('/api/test', expect.objectContaining({
        credentials: 'include',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }))
      expect(result).toEqual({ id: 1 })
    })

    it('does NOT inject X-CSRF-Token on GET', async () => {
      Object.defineProperty(document, 'cookie', {
        writable: true,
        value: 'oqim_csrf=my-token',
      })
      const fetchMock = mockFetch({ status: 200, ok: true, json: () => Promise.resolve({}) })

      await api.get('/api/test')

      const calledHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>
      expect(calledHeaders['X-CSRF-Token']).toBeUndefined()
    })
  })

  describe('POST requests', () => {
    it('injects X-CSRF-Token header when oqim_csrf cookie is present', async () => {
      Object.defineProperty(document, 'cookie', {
        writable: true,
        value: 'oqim_csrf=abc123',
      })
      const fetchMock = mockFetch({ status: 200, ok: true, json: () => Promise.resolve({}) })

      await api.post('/api/test', { foo: 'bar' })

      const calledHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>
      expect(calledHeaders['X-CSRF-Token']).toBe('abc123')
    })

    it('serializes body to JSON', async () => {
      const fetchMock = mockFetch({ status: 200, ok: true, json: () => Promise.resolve({}) })

      await api.post('/api/test', { key: 'value' })

      expect(fetchMock.mock.calls[0][1].body).toBe('{"key":"value"}')
    })

    it('does not set body when no body provided', async () => {
      const fetchMock = mockFetch({ status: 200, ok: true, json: () => Promise.resolve({}) })

      await api.post('/api/test')

      expect(fetchMock.mock.calls[0][1].body).toBeUndefined()
    })
  })

  describe('error handling', () => {
    it('throws ApiError with status 401 on unauthorized', async () => {
      mockFetch({ status: 401, ok: false })

      await expect(api.get('/api/protected')).rejects.toMatchObject({
        name: 'ApiError',
        status: 401,
      })
    })

    it('throws ApiError with response data on non-ok responses', async () => {
      mockFetch({
        status: 422,
        ok: false,
        json: () => Promise.resolve({ detail: 'Validation error' }),
      })

      await expect(api.post('/api/test', {})).rejects.toMatchObject({
        name: 'ApiError',
        status: 422,
        data: { detail: 'Validation error' },
      })
    })

    it('returns undefined for 204 No Content', async () => {
      mockFetch({ status: 204, ok: true })

      const result = await api.delete('/api/test/1')
      expect(result).toBeUndefined()
    })
  })

  describe('ApiError class', () => {
    it('has correct name, status, statusText, and data', () => {
      const err = new ApiError(404, 'Not Found', { detail: 'missing' })
      expect(err.name).toBe('ApiError')
      expect(err.status).toBe(404)
      expect(err.statusText).toBe('Not Found')
      expect(err.data).toEqual({ detail: 'missing' })
      expect(err.message).toBe('404 Not Found')
    })
  })
})
