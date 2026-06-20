export const SEND_IDEMPOTENCY_TABLE = 'telegram_sidecar_send_idempotency';

export const CREATE_SEND_IDEMPOTENCY_SQL = `
CREATE TABLE IF NOT EXISTS telegram_sidecar_send_idempotency (
  workspace_id BIGINT NOT NULL,
  idempotency_key TEXT NOT NULL,
  response JSONB NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_telegram_sidecar_send_idempotency_expires
  ON telegram_sidecar_send_idempotency (expires_at);
`;

export function createDurableSendIdempotencyStore({
  pool,
  memoryCache,
  ttlSeconds = 7 * 24 * 60 * 60,
} = {}) {
  if (!pool) {
    throw new Error('pool is required');
  }
  if (!memoryCache) {
    throw new Error('memoryCache is required');
  }

  let schemaReady = null;

  async function ensureSchema() {
    if (!schemaReady) {
      schemaReady = pool.query(CREATE_SEND_IDEMPOTENCY_SQL).catch((err) => {
        schemaReady = null;
        throw err;
      });
    }
    await schemaReady;
  }

  async function get(workspaceId, idempotencyKey) {
    const cached = memoryCache.get(workspaceId, idempotencyKey);
    if (cached) {
      return cached;
    }
    if (!workspaceId || !idempotencyKey) {
      return null;
    }

    await ensureSchema();
    const result = await pool.query(
      `SELECT response
       FROM telegram_sidecar_send_idempotency
       WHERE workspace_id = $1
         AND idempotency_key = $2
         AND expires_at > NOW()
       LIMIT 1`,
      [workspaceId, idempotencyKey],
    );
    const row = result.rows?.[0];
    if (!row) {
      return null;
    }
    memoryCache.rememberResult(workspaceId, idempotencyKey, row.response);
    return { response: row.response, durable: true };
  }

  async function rememberResult(workspaceId, idempotencyKey, response) {
    memoryCache.rememberResult(workspaceId, idempotencyKey, response);
    if (!workspaceId || !idempotencyKey) {
      return;
    }
    await ensureSchema();
    await pool.query(
      `INSERT INTO telegram_sidecar_send_idempotency
         (workspace_id, idempotency_key, response, expires_at)
       VALUES ($1, $2, $3::jsonb, NOW() + ($4::int * INTERVAL '1 second'))
       ON CONFLICT (workspace_id, idempotency_key)
       DO UPDATE SET
         response = EXCLUDED.response,
         expires_at = EXCLUDED.expires_at,
         updated_at = NOW()`,
      [workspaceId, idempotencyKey, JSON.stringify(response), ttlSeconds],
    );
  }

  async function rememberPromise(workspaceId, idempotencyKey, promise) {
    memoryCache.rememberPromise(workspaceId, idempotencyKey, promise);
  }

  async function forget(workspaceId, idempotencyKey) {
    memoryCache.forget(workspaceId, idempotencyKey);
    if (!workspaceId || !idempotencyKey) {
      return;
    }
    await ensureSchema();
    await pool.query(
      `DELETE FROM telegram_sidecar_send_idempotency
       WHERE workspace_id = $1
         AND idempotency_key = $2`,
      [workspaceId, idempotencyKey],
    );
  }

  return {
    ensureSchema,
    get,
    rememberPromise,
    rememberResult,
    forget,
  };
}
