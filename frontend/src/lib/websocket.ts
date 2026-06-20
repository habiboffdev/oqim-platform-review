type EventHandler = (data: Record<string, unknown>) => void

interface WSEvent {
  type: string
  data: Record<string, unknown>
}

const PING_INTERVAL = 30_000
const PONG_TIMEOUT = 10_000

class WebSocketManager {
  private ws: WebSocket | null = null
  private handlers = new Map<string, Set<EventHandler>>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDelay = 1000
  private maxReconnectDelay = 30000
  private wasConnected = false
  private url: string
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private pongTimer: ReturnType<typeof setTimeout> | null = null

  constructor(url: string) {
    this.url = url
    this.setupVisibilityHandler()
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return

    try {
      this.ws = new WebSocket(this.url)

      this.ws.onopen = () => {
        if (this.wasConnected) {
          const reconnectHandlers = this.handlers.get('reconnect')
          if (reconnectHandlers) {
            reconnectHandlers.forEach((handler) => handler({}))
          }
        }
        this.wasConnected = true
        this.reconnectDelay = 1000
        this.startPing()
      }

      this.ws.onmessage = (event) => {
        try {
          const parsed: WSEvent = JSON.parse(event.data)

          // Any message from server proves connection is alive
          this.clearPongTimeout()

          if (parsed.type === 'pong') return

          const handlers = this.handlers.get(parsed.type)
          if (handlers) {
            handlers.forEach((handler) => handler(parsed.data))
          }
          // Also notify wildcard listeners — merge top-level fields (type, scope,
          // message, ts) AND nested data so both activity events and operational
          // handlers find the fields they need.
          const wildcardHandlers = this.handlers.get('*')
          if (wildcardHandlers) {
            wildcardHandlers.forEach((handler) => handler({ ...parsed, ...((parsed.data as Record<string, unknown>) || {}) }))
          }
        } catch {
          // ignore malformed messages
        }
      }

      this.ws.onclose = (event) => {
        this.stopPing()
        if (event.code === 4001) {
          const handlers = this.handlers.get('auth_failure')
          if (handlers) {
            handlers.forEach((handler) => handler({}))
          }
          return
        }
        this.scheduleReconnect()
      }

      this.ws.onerror = () => {
        this.ws?.close()
      }
    } catch {
      this.scheduleReconnect()
    }
  }

  private startPing() {
    this.stopPing()
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
        // Expect a response within PONG_TIMEOUT
        this.pongTimer = setTimeout(() => {
          this.ws?.close()
        }, PONG_TIMEOUT)
      }
    }, PING_INTERVAL)
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    this.clearPongTimeout()
  }

  private clearPongTimeout() {
    if (this.pongTimer) {
      clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  private setupVisibilityHandler() {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        // Tab became visible — check if WS is still alive
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
          this.reconnectDelay = 1000
          this.connect()
        }
      }
    })
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
      this.connect()
    }, this.reconnectDelay)
  }

  disconnect() {
    this.stopPing()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  send(data: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  on(event: string, handler: EventHandler) {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set())
    }
    this.handlers.get(event)!.add(handler)
    return () => {
      this.handlers.get(event)?.delete(handler)
    }
  }

  off(event: string, handler: EventHandler) {
    this.handlers.get(event)?.delete(handler)
  }
}

export const wsManager = new WebSocketManager(
  `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/ws`,
)
