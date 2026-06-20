function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)oqim_csrf=([^;]*)/)
  return match ? decodeURIComponent(match[1]) : null
}

export class ApiError extends Error {
  status: number
  statusText: string
  data?: unknown

  constructor(status: number, statusText: string, data?: unknown) {
    super(`${status} ${statusText}`)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
    this.data = data
  }
}

const MUTATION_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const providedHeaders = (options.headers as Record<string, string>) ?? {}
  const headers: Record<string, string> = {
    ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...providedHeaders,
  }

  const method = (options.method ?? 'GET').toUpperCase()
  if (MUTATION_METHODS.has(method)) {
    const csrf = getCsrfToken()
    if (csrf) {
      headers['X-CSRF-Token'] = csrf
    }
  }

  const res = await fetch(path, { ...options, headers, credentials: 'include' })

  if (res.status === 401) {
    throw new ApiError(401, 'Unauthorized')
  }

  if (!res.ok) {
    let data: unknown
    try {
      data = await res.json()
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, res.statusText, data)
  }

  if (res.status === 204) return undefined as T

  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PUT',
      body: body ? JSON.stringify(body) : undefined,
    }),

  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PATCH',
      body: body ? JSON.stringify(body) : undefined,
    }),

  delete: <T>(path: string) =>
    request<T>(path, { method: 'DELETE' }),

  upload: <T>(path: string, form: FormData) =>
    request<T>(path, {
      method: 'POST',
      body: form,
      headers: {}, // let browser set Content-Type with multipart boundary
    }),
}
