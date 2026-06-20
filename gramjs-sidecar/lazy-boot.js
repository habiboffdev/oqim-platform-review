export const DEFAULT_LAZY_BOOT_ACTIVE_HOURS = 24;
export const DEFAULT_MAX_STORED_SESSION_BYTES = 10 * 1024 * 1024;

export const RECENTLY_ACTIVE_WORKSPACE_IDS_SQL = `
SELECT ts.workspace_id
FROM telegram_sessions ts
WHERE ts.updated_at >= NOW() - ($1::int * INTERVAL '1 hour')
   OR EXISTS (
     SELECT 1
     FROM conversations c
     WHERE c.workspace_id = ts.workspace_id
       AND c.last_message_at >= NOW() - ($1::int * INTERVAL '1 hour')
   )
   OR EXISTS (
     SELECT 1
     FROM conversations c
     JOIN messages m ON m.conversation_id = c.id
     WHERE c.workspace_id = ts.workspace_id
       AND COALESCE(m.telegram_timestamp, m.created_at) >= NOW() - ($1::int * INTERVAL '1 hour')
   )
ORDER BY ts.workspace_id
`;

export function normalizeLazyBootHours(value, fallback = DEFAULT_LAZY_BOOT_ACTIVE_HOURS) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return parsed;
}

export async function listRecentlyActiveWorkspaceIds(pool, activeHours, logger = console) {
  try {
    const res = await pool.query(RECENTLY_ACTIVE_WORKSPACE_IDS_SQL, [activeHours]);
    return res.rows.map((row) => row.workspace_id);
  } catch (err) {
    logger.warn?.('[Session] Failed to list recently active sessions:', err.message);
    return [];
  }
}

// Every workspace that has a persisted session. Used on boot so a session that
// has merely been idle (e.g. the server was paused for >activeHours) is still
// restored — the recency window above is an optimization that must NOT silently
// drop a real authorized session the owner still wants live. A dead/revoked one
// just fails its connect and falls into the normal reconnect/backoff path.
export const ALL_STORED_SESSION_WORKSPACE_IDS_SQL = `
SELECT ts.workspace_id
FROM telegram_sessions ts
JOIN workspaces w ON w.id = ts.workspace_id
WHERE ts.session_data IS NOT NULL AND ts.session_data <> ''
  AND octet_length(ts.session_data) <= $1
  AND COALESCE(w.telegram_connected, FALSE) IS TRUE
ORDER BY ts.workspace_id
`;

export const OVERSIZED_STORED_SESSION_WORKSPACE_IDS_SQL = `
SELECT ts.workspace_id, octet_length(ts.session_data) AS session_bytes
FROM telegram_sessions ts
JOIN workspaces w ON w.id = ts.workspace_id
WHERE ts.session_data IS NOT NULL AND ts.session_data <> ''
  AND octet_length(ts.session_data) > $1
  AND COALESCE(w.telegram_connected, FALSE) IS TRUE
ORDER BY ts.workspace_id
`;

export const STORED_SESSION_ENABLED_SQL = `
SELECT COALESCE(w.telegram_connected, FALSE) AS enabled
FROM workspaces w
JOIN telegram_sessions ts ON ts.workspace_id = w.id
WHERE w.id = $1
  AND ts.session_data IS NOT NULL
  AND ts.session_data <> ''
`;

export async function listStoredSessionWorkspaceIds(
  pool,
  logger = console,
  maxSessionBytes = DEFAULT_MAX_STORED_SESSION_BYTES,
) {
  try {
    const oversized = await pool.query(
      OVERSIZED_STORED_SESSION_WORKSPACE_IDS_SQL,
      [maxSessionBytes],
    );
    for (const row of oversized.rows || []) {
      logger.warn?.(
        `[Session] Skipping oversized stored session for workspace ${row.workspace_id}: `
          + `${row.session_bytes} bytes exceeds ${maxSessionBytes}`,
      );
    }
    const res = await pool.query(ALL_STORED_SESSION_WORKSPACE_IDS_SQL, [maxSessionBytes]);
    return res.rows.map((row) => row.workspace_id);
  } catch (err) {
    logger.warn?.('[Session] Failed to list stored sessions:', err.message);
    return [];
  }
}

export async function isStoredSessionEnabled(pool, workspaceId, logger = console) {
  try {
    const res = await pool.query(STORED_SESSION_ENABLED_SQL, [workspaceId]);
    return Boolean(res.rows?.[0]?.enabled);
  } catch (err) {
    logger.warn?.(
      `[Session] Failed to read stored-session enablement for workspace ${workspaceId}:`,
      err.message,
    );
    return false;
  }
}
