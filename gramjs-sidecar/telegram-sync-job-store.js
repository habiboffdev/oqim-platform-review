import { parseFloodWaitSeconds } from './telegram-errors.js';

export const CREATE_TELEGRAM_SYNC_JOB_SQL = `
CREATE TABLE IF NOT EXISTS telegram_sidecar_sync_jobs (
  workspace_id BIGINT NOT NULL,
  job_kind TEXT NOT NULL,
  job_key TEXT NOT NULL,
  method_class TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 4,
  cursor JSONB NOT NULL DEFAULT '{}'::jsonb,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  retry_after_seconds INTEGER,
  next_attempt_at DOUBLE PRECISION,
  started_at DOUBLE PRECISION,
  finished_at DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, job_kind, job_key)
);
CREATE INDEX IF NOT EXISTS idx_telegram_sidecar_sync_jobs_workspace_status
  ON telegram_sidecar_sync_jobs (workspace_id, status, next_attempt_at);
`;

function nowSeconds() {
  return Date.now() / 1000;
}

function safeJson(value) {
  return JSON.stringify(value || {}, (_key, entry) => (
    typeof entry === 'bigint' ? entry.toString() : entry
  ));
}

function normalizeJobInput({
  runtime,
  jobKind,
  jobKey,
  methodClass,
  priority = 4,
  cursor = {},
}) {
  return {
    runtime,
    workspaceId: runtime?.workspaceId || null,
    jobKind: jobKind || methodClass || 'telegram_sync',
    jobKey: jobKey || methodClass || 'default',
    methodClass: methodClass || jobKind || 'telegram_sync',
    priority,
    cursor,
  };
}

function ensureRuntimeSyncJobCounts(runtime) {
  if (!runtime) return null;
  if (!runtime.telegramSyncJobCounts) {
    runtime.telegramSyncJobCounts = {
      started: 0,
      succeeded: 0,
      failed: 0,
      paused: 0,
    };
  }
  return runtime.telegramSyncJobCounts;
}

function incrementRuntimeSyncJob(runtime, field) {
  const counts = ensureRuntimeSyncJobCounts(runtime);
  if (!counts) return;
  counts[field] = (counts[field] || 0) + 1;
}

function statusForError(err) {
  const retryAfter = err?.retryAfter || parseFloodWaitSeconds(err);
  return {
    status: retryAfter ? 'paused' : 'failed',
    retryAfter: retryAfter || null,
    nextAttemptAt: retryAfter ? nowSeconds() + retryAfter : null,
  };
}

function emptySummary() {
  return {
    running: 0,
    paused: 0,
    failed: 0,
    succeeded: 0,
  };
}

function asNumber(value) {
  if (value == null) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function normalizeCursor(value) {
  if (!value) return {};
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  return value;
}

function rowToSyncJob(row) {
  return {
    workspaceId: asNumber(row.workspace_id),
    jobKind: row.job_kind,
    jobKey: row.job_key,
    methodClass: row.method_class,
    status: row.status,
    priority: asNumber(row.priority) ?? 4,
    cursor: normalizeCursor(row.cursor),
    attempts: asNumber(row.attempts) ?? 0,
    lastError: row.last_error ?? null,
    retryAfterSeconds: asNumber(row.retry_after_seconds),
    nextAttemptAt: asNumber(row.next_attempt_at),
  };
}

export function createTelegramSyncJobStore({ pool } = {}) {
  if (!pool) {
    throw new Error('pool is required');
  }

  let schemaReady = null;

  async function ensureSchema() {
    if (!schemaReady) {
      schemaReady = pool.query(CREATE_TELEGRAM_SYNC_JOB_SQL).catch((err) => {
        schemaReady = null;
        throw err;
      });
    }
    await schemaReady;
  }

  async function recordStart(input) {
    const job = normalizeJobInput(input);
    if (!job.workspaceId) return false;

    await ensureSchema();
    await pool.query(
      `INSERT INTO telegram_sidecar_sync_jobs (
         workspace_id, job_kind, job_key, method_class, status, priority,
         cursor, attempts, last_error, retry_after_seconds, next_attempt_at,
         started_at, finished_at, updated_at
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 1, NULL, NULL, NULL, $8, NULL, NOW())
       ON CONFLICT (workspace_id, job_kind, job_key)
       DO UPDATE SET
         method_class = EXCLUDED.method_class,
         status = EXCLUDED.status,
         priority = EXCLUDED.priority,
         cursor = EXCLUDED.cursor,
         attempts = telegram_sidecar_sync_jobs.attempts + 1,
         last_error = NULL,
         retry_after_seconds = NULL,
         next_attempt_at = NULL,
         started_at = EXCLUDED.started_at,
         finished_at = NULL,
         updated_at = NOW()`,
      [
        job.workspaceId,
        job.jobKind,
        job.jobKey,
        job.methodClass,
        'running',
        job.priority,
        safeJson(job.cursor),
        nowSeconds(),
      ],
    );
    incrementRuntimeSyncJob(job.runtime, 'started');
    return true;
  }

  async function recordFinish(input, {
    status,
    cursor,
    lastError = null,
    retryAfter = null,
    nextAttemptAt = null,
  }) {
    const job = normalizeJobInput(input);
    if (!job.workspaceId) return false;

    await ensureSchema();
    await pool.query(
      `UPDATE telegram_sidecar_sync_jobs
       SET status = $1,
           cursor = $2::jsonb,
           last_error = $3,
           retry_after_seconds = $4,
           next_attempt_at = $5,
           finished_at = $6,
           updated_at = NOW()
       WHERE workspace_id = $7 AND job_kind = $8 AND job_key = $9`,
      [
        status,
        safeJson(cursor ?? job.cursor),
        lastError,
        retryAfter,
        nextAttemptAt,
        nowSeconds(),
        job.workspaceId,
        job.jobKind,
        job.jobKey,
      ],
    );
    incrementRuntimeSyncJob(job.runtime, status === 'paused' ? 'paused' : status);
    return true;
  }

  async function runSyncJob(input, fn) {
    const job = normalizeJobInput(input);
    if (!job.workspaceId) {
      return fn();
    }

    await recordStart(job);
    try {
      const result = await fn();
      await recordFinish(job, {
        status: 'succeeded',
        cursor: job.cursor,
      });
      job.runtime.lastSyncJobError = null;
      return result;
    } catch (err) {
      const { status, retryAfter, nextAttemptAt } = statusForError(err);
      await recordFinish(job, {
        status,
        cursor: job.cursor,
        lastError: err.message || String(err),
        retryAfter,
        nextAttemptAt,
      });
      job.runtime.lastSyncJobError = err.message || String(err);
      throw err;
    }
  }

  async function summaryForWorkspace(workspaceId) {
    if (!workspaceId) {
      return emptySummary();
    }

    await ensureSchema();
    const result = await pool.query(
      `SELECT status, COUNT(*)::int AS count
       FROM telegram_sidecar_sync_jobs
       WHERE workspace_id = $1
       GROUP BY status`,
      [workspaceId],
    );
    const summary = emptySummary();
    for (const row of result.rows || []) {
      if (Object.hasOwn(summary, row.status)) {
        summary[row.status] = Number(row.count || 0);
      }
    }
    return summary;
  }

  async function listResumableJobs(workspaceId, { now = nowSeconds(), limit = 20 } = {}) {
    if (!workspaceId) {
      return [];
    }

    await ensureSchema();
    const result = await pool.query(
      `SELECT workspace_id, job_kind, job_key, method_class, status, priority,
              cursor, attempts, last_error, retry_after_seconds, next_attempt_at
       FROM telegram_sidecar_sync_jobs
       WHERE workspace_id = $1
         AND (
           status = 'running'
           OR status = 'failed'
           OR (status = 'paused' AND (next_attempt_at IS NULL OR next_attempt_at <= $2))
         )
       ORDER BY priority ASC, COALESCE(next_attempt_at, 0) ASC, updated_at ASC
       LIMIT $3`,
      [workspaceId, now, limit],
    );
    return (result.rows || []).map(rowToSyncJob);
  }

  return {
    ensureSchema,
    listResumableJobs,
    recordFinish,
    recordStart,
    runSyncJob,
    summaryForWorkspace,
  };
}

export function createDurableTelegramMethodRunner({
  syncJobStore,
  runQueuedTelegramMethod,
}) {
  if (!runQueuedTelegramMethod) {
    throw new Error('runQueuedTelegramMethod is required');
  }

  return async function runDurableTelegramMethod(runtime, meta, fn) {
    if (!syncJobStore || meta?.trackSyncJob === false) {
      return runQueuedTelegramMethod(runtime, meta, fn);
    }

    return syncJobStore.runSyncJob(
      {
        runtime,
        jobKind: meta?.jobKind || meta?.methodClass,
        jobKey: meta?.jobKey || meta?.label || meta?.methodClass,
        methodClass: meta?.methodClass,
        priority: meta?.priority,
        cursor: meta?.cursor || {},
      },
      () => runQueuedTelegramMethod(runtime, meta, fn),
    );
  };
}
