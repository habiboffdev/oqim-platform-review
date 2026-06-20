const DEFAULT_FLUSH_INTERVAL_MS = 5000;
const DEFAULT_LEASE_MS = 30_000;
const DEFAULT_MAX_BACKOFF_MS = 300_000;
const DEFAULT_BATCH_SIZE = 50;

export const EVENT_OUTBOX_TABLE = 'telegram_sidecar_event_outbox';

export const CREATE_EVENT_OUTBOX_SQL = `
CREATE TABLE IF NOT EXISTS telegram_sidecar_event_outbox (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL,
  event_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  payload JSONB NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  leased_until TIMESTAMPTZ NULL,
  last_error TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telegram_sidecar_event_outbox_due
  ON telegram_sidecar_event_outbox (next_attempt_at, workspace_id, id);
`;

function backoffSeconds(attempts, maxBackoffMs) {
  const seconds = Math.min(2 ** Math.max(0, attempts), Math.ceil(maxBackoffMs / 1000));
  return Math.max(1, seconds);
}

export class DurableEventOutbox {
  constructor(pool, forwardFn, opts = {}) {
    this._pool = pool;
    this._forwardFn = forwardFn;
    this._flushIntervalMs = opts.flushIntervalMs ?? DEFAULT_FLUSH_INTERVAL_MS;
    this._leaseMs = opts.leaseMs ?? DEFAULT_LEASE_MS;
    this._maxBackoffMs = opts.maxBackoffMs ?? DEFAULT_MAX_BACKOFF_MS;
    this._batchSize = opts.batchSize ?? DEFAULT_BATCH_SIZE;
    this._timer = null;
    this._schemaReady = null;
  }

  async ensureSchema() {
    if (!this._schemaReady) {
      this._schemaReady = this._pool.query(CREATE_EVENT_OUTBOX_SQL).catch((err) => {
        this._schemaReady = null;
        throw err;
      });
    }
    await this._schemaReady;
  }

  async enqueue({ workspaceId, eventType, idempotencyKey, path, payload }) {
    if (!workspaceId || !eventType || !idempotencyKey || !path || !payload) {
      throw new Error('workspaceId, eventType, idempotencyKey, path, and payload are required');
    }
    await this.ensureSchema();
    await this._pool.query(
      `INSERT INTO telegram_sidecar_event_outbox
         (workspace_id, event_type, idempotency_key, path, payload)
       VALUES ($1, $2, $3, $4, $5::jsonb)
       ON CONFLICT (idempotency_key) DO NOTHING`,
      [workspaceId, eventType, idempotencyKey, path, JSON.stringify(payload)],
    );
  }

  async pendingCount(workspaceId = null) {
    await this.ensureSchema();
    if (workspaceId) {
      const result = await this._pool.query(
        'SELECT COUNT(*)::int AS count FROM telegram_sidecar_event_outbox WHERE workspace_id = $1',
        [workspaceId],
      );
      return Number(result.rows?.[0]?.count || 0);
    }
    const result = await this._pool.query(
      'SELECT COUNT(*)::int AS count FROM telegram_sidecar_event_outbox',
    );
    return Number(result.rows?.[0]?.count || 0);
  }

  async flush({ workspaceId = null, limit = this._batchSize } = {}) {
    await this.ensureSchema();
    const leaseSeconds = Math.max(1, Math.ceil(this._leaseMs / 1000));
    const result = await this._pool.query(
      `WITH due AS (
         SELECT id
         FROM telegram_sidecar_event_outbox
         WHERE next_attempt_at <= NOW()
           AND (leased_until IS NULL OR leased_until < NOW())
           AND ($1::bigint IS NULL OR workspace_id = $1::bigint)
         ORDER BY id
         LIMIT $2
         FOR UPDATE SKIP LOCKED
       )
       UPDATE telegram_sidecar_event_outbox outbox
       SET leased_until = NOW() + ($3::int * INTERVAL '1 second'),
           updated_at = NOW()
       FROM due
       WHERE outbox.id = due.id
       RETURNING outbox.id, outbox.path, outbox.payload, outbox.attempts`,
      [workspaceId, limit, leaseSeconds],
    );

    let delivered = 0;
    for (const row of result.rows || []) {
      try {
        await this._forwardFn(row.path, row.payload);
        await this._pool.query(
          'DELETE FROM telegram_sidecar_event_outbox WHERE id = $1',
          [row.id],
        );
        delivered += 1;
      } catch (err) {
        const attempts = Number(row.attempts || 0) + 1;
        await this._pool.query(
          `UPDATE telegram_sidecar_event_outbox
           SET attempts = $2,
               next_attempt_at = NOW() + ($3::int * INTERVAL '1 second'),
               leased_until = NULL,
               last_error = $4,
               updated_at = NOW()
           WHERE id = $1`,
          [row.id, attempts, backoffSeconds(attempts, this._maxBackoffMs), err.message || String(err)],
        );
      }
    }
    return delivered;
  }

  start() {
    if (this._timer) return;
    const tick = async () => {
      try {
        await this.flush();
      } catch (err) {
        console.warn('[Outbox] Flush failed:', err.message);
      } finally {
        this._timer = setTimeout(tick, this._flushIntervalMs);
      }
    };
    this._timer = setTimeout(tick, this._flushIntervalMs);
  }

  stop() {
    if (!this._timer) return;
    clearTimeout(this._timer);
    this._timer = null;
  }
}
