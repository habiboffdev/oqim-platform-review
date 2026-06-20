import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  CREATE_TELEGRAM_SYNC_JOB_SQL,
  createDurableTelegramMethodRunner,
  createTelegramSyncJobStore,
} from './telegram-sync-job-store.js';

function makePool() {
  const calls = [];
  return {
    calls,
    async query(sql, params = []) {
      calls.push({ sql, params });
      return { rows: [] };
    },
  };
}

describe('telegram sync job store', () => {
  it('creates durable sync job state outside the compact auth session table', async () => {
    const pool = makePool();
    const store = createTelegramSyncJobStore({ pool });

    await store.ensureSchema();

    assert.match(CREATE_TELEGRAM_SYNC_JOB_SQL, /telegram_sidecar_sync_jobs/);
    assert.doesNotMatch(CREATE_TELEGRAM_SYNC_JOB_SQL, /telegram_sessions/);
    assert.equal(pool.calls.length, 1);
  });

  it('records a resumable sync job from running to succeeded', async () => {
    const pool = makePool();
    const runtime = { workspaceId: 7 };
    const store = createTelegramSyncJobStore({ pool });

    const result = await store.runSyncJob(
      {
        runtime,
        jobKind: 'dialog_sync',
        jobKey: 'dialogs',
        methodClass: 'dialog_sync',
        priority: 3,
        cursor: { page: 1 },
      },
      async () => ['ok'],
    );

    assert.deepEqual(result, ['ok']);
    const start = pool.calls.find((call) => /INSERT INTO telegram_sidecar_sync_jobs/.test(call.sql));
    const finish = pool.calls.filter((call) => /UPDATE telegram_sidecar_sync_jobs/.test(call.sql)).at(-1);
    assert.deepEqual(start.params.slice(0, 6), [
      7,
      'dialog_sync',
      'dialogs',
      'dialog_sync',
      'running',
      3,
    ]);
    assert.deepEqual(JSON.parse(start.params[6]), { page: 1 });
    assert.equal(finish.params[0], 'succeeded');
    assert.equal(finish.params[2], null);
    assert.deepEqual(runtime.telegramSyncJobCounts, {
      started: 1,
      succeeded: 1,
      failed: 0,
      paused: 0,
    });
    assert.equal(runtime.lastSyncJobError, null);
  });

  it('records flood-waited sync jobs as paused with retry metadata', async () => {
    const pool = makePool();
    const runtime = { workspaceId: 7 };
    const store = createTelegramSyncJobStore({ pool });

    await assert.rejects(
      () => store.runSyncJob(
        {
          runtime,
          jobKind: 'history_sync',
          jobKey: 'unread:444',
          methodClass: 'history_sync',
          priority: 4,
          cursor: { limit: 50 },
        },
        async () => {
          throw Object.assign(new Error('FLOOD_WAIT_11'), { seconds: 11 });
        },
      ),
      /FLOOD_WAIT_11/,
    );

    const finish = pool.calls.filter((call) => /UPDATE telegram_sidecar_sync_jobs/.test(call.sql)).at(-1);
    assert.equal(finish.params[0], 'paused');
    assert.equal(finish.params[2], 'FLOOD_WAIT_11');
    assert.equal(finish.params[3], 11);
    assert.equal(runtime.telegramSyncJobCounts.paused, 1);
    assert.equal(runtime.lastSyncJobError, 'FLOOD_WAIT_11');
  });

  it('wraps queued Telegram methods with durable job metadata', async () => {
    const calls = [];
    const runner = createDurableTelegramMethodRunner({
      syncJobStore: {
        runSyncJob: async (input, fn) => {
          calls.push({ syncJob: input });
          return fn();
        },
      },
      runQueuedTelegramMethod: async (_runtime, meta, fn) => {
        calls.push({ queued: meta });
        return fn();
      },
    });

    const result = await runner(
      { workspaceId: 7 },
      {
        methodClass: 'history_sync',
        label: 'GET_MESSAGES_7',
        priority: 4,
        cursor: { afterId: 10 },
      },
      async () => 'done',
    );

    assert.equal(result, 'done');
    assert.equal(calls[0].syncJob.jobKind, 'history_sync');
    assert.equal(calls[0].syncJob.jobKey, 'GET_MESSAGES_7');
    assert.deepEqual(calls[0].syncJob.cursor, { afterId: 10 });
    assert.equal(calls[1].queued.label, 'GET_MESSAGES_7');
  });

  it('summarizes persisted sync jobs by status for operator status', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /FROM telegram_sidecar_sync_jobs/);
        assert.deepEqual(params, [7]);
        return {
          rows: [
            { status: 'running', count: '2' },
            { status: 'paused', count: '1' },
            { status: 'succeeded', count: '5' },
          ],
        };
      },
    };
    const store = createTelegramSyncJobStore({ pool });

    assert.deepEqual(await store.summaryForWorkspace(7), {
      running: 2,
      paused: 1,
      failed: 0,
      succeeded: 5,
    });
  });

  it('lists due persisted jobs that can resume after restart', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /FROM telegram_sidecar_sync_jobs/);
        assert.match(sql, /next_attempt_at <= \$2/);
        assert.deepEqual(params, [7, 123, 10]);
        return {
          rows: [
            {
              workspace_id: '7',
              job_kind: 'unread_catchup',
              job_key: 'dialogs',
              method_class: 'dialog_sync',
              status: 'paused',
              priority: 3,
              cursor: { dialogPage: 1 },
              attempts: '2',
              last_error: 'FLOOD_WAIT_3',
              retry_after_seconds: 3,
              next_attempt_at: 120,
            },
            {
              workspace_id: '7',
              job_kind: 'dialog_sync',
              job_key: 'dialog_shells',
              method_class: 'dialog_sync',
              status: 'running',
              priority: 3,
              cursor: {},
              attempts: 1,
              last_error: null,
              retry_after_seconds: null,
              next_attempt_at: null,
            },
          ],
        };
      },
    };
    const store = createTelegramSyncJobStore({ pool });

    assert.deepEqual(await store.listResumableJobs(7, { now: 123, limit: 10 }), [
      {
        workspaceId: 7,
        jobKind: 'unread_catchup',
        jobKey: 'dialogs',
        methodClass: 'dialog_sync',
        status: 'paused',
        priority: 3,
        cursor: { dialogPage: 1 },
        attempts: 2,
        lastError: 'FLOOD_WAIT_3',
        retryAfterSeconds: 3,
        nextAttemptAt: 120,
      },
      {
        workspaceId: 7,
        jobKind: 'dialog_sync',
        jobKey: 'dialog_shells',
        methodClass: 'dialog_sync',
        status: 'running',
        priority: 3,
        cursor: {},
        attempts: 1,
        lastError: null,
        retryAfterSeconds: null,
        nextAttemptAt: null,
      },
    ]);
  });
});
