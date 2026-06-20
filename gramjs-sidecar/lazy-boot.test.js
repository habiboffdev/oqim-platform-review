import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  ALL_STORED_SESSION_WORKSPACE_IDS_SQL,
  RECENTLY_ACTIVE_WORKSPACE_IDS_SQL,
  STORED_SESSION_ENABLED_SQL,
  isStoredSessionEnabled,
  listRecentlyActiveWorkspaceIds,
  listStoredSessionWorkspaceIds,
  normalizeLazyBootHours,
  OVERSIZED_STORED_SESSION_WORKSPACE_IDS_SQL,
} from './lazy-boot.js';

describe('lazy boot session restore policy', () => {
  it('normalizes invalid configured windows to the 24h default', () => {
    assert.equal(normalizeLazyBootHours(undefined), 24);
    assert.equal(normalizeLazyBootHours('0'), 24);
    assert.equal(normalizeLazyBootHours('-5'), 24);
    assert.equal(normalizeLazyBootHours('48'), 48);
  });

  it('queries only recently active workspaces', async () => {
    const queries = [];
    const pool = {
      async query(sql, params) {
        queries.push({ sql, params });
        return { rows: [{ workspace_id: 12 }, { workspace_id: 45 }] };
      },
    };

    const ids = await listRecentlyActiveWorkspaceIds(pool, 24);

    assert.deepEqual(ids, [12, 45]);
    assert.deepEqual(queries[0].params, [24]);
    assert.match(queries[0].sql, /telegram_sessions ts/);
    assert.match(queries[0].sql, /last_message_at/);
    assert.match(queries[0].sql, /COALESCE\(m.telegram_timestamp, m.created_at\)/);
  });

  it('fails closed instead of reconnecting every tenant when the activity query breaks', async () => {
    const warnings = [];
    const pool = {
      async query() {
        throw new Error('schema drift');
      },
    };

    const ids = await listRecentlyActiveWorkspaceIds(pool, 24, {
      warn: (...args) => warnings.push(args.join(' ')),
    });

    assert.deepEqual(ids, []);
    assert.match(warnings[0], /Failed to list recently active sessions/);
  });

  it('keeps the SQL bounded by session activity, conversation recency, and message recency', () => {
    assert.match(RECENTLY_ACTIVE_WORKSPACE_IDS_SQL, /ts\.updated_at >= NOW\(\)/);
    assert.match(RECENTLY_ACTIVE_WORKSPACE_IDS_SQL, /conversations c/);
    assert.match(RECENTLY_ACTIVE_WORKSPACE_IDS_SQL, /JOIN messages m/);
  });

  it('restores every enabled workspace with a stored session, regardless of recency', async () => {
    const queries = [];
    const pool = {
      async query(sql, params) {
        queries.push({ sql, params });
        if (sql === OVERSIZED_STORED_SESSION_WORKSPACE_IDS_SQL) {
          return { rows: [] };
        }
        return { rows: [{ workspace_id: 1 }, { workspace_id: 3 }, { workspace_id: 7 }] };
      },
    };

    const ids = await listStoredSessionWorkspaceIds(pool);

    assert.deepEqual(ids, [1, 3, 7]);
    assert.deepEqual(queries[0].params, [10 * 1024 * 1024]);
    assert.match(queries[0].sql, /octet_length\(ts\.session_data\) > \$1/);
    assert.deepEqual(queries[1].params, [10 * 1024 * 1024]);
    assert.match(queries[1].sql, /telegram_sessions/);
    assert.match(queries[1].sql, /workspaces w/);
    assert.match(queries[1].sql, /telegram_connected/);
    assert.match(queries[1].sql, /session_data IS NOT NULL/);
    assert.match(queries[1].sql, /octet_length\(ts\.session_data\) <= \$1/);
  });

  it('skips oversized stored sessions so one tenant cannot crash sidecar boot', async () => {
    const warnings = [];
    const pool = {
      async query(sql, params) {
        if (sql === OVERSIZED_STORED_SESSION_WORKSPACE_IDS_SQL) {
          return { rows: [{ workspace_id: 3, session_bytes: 450765200 }] };
        }
        return { rows: [{ workspace_id: 1 }] };
      },
    };

    const ids = await listStoredSessionWorkspaceIds(pool, {
      warn: (...args) => warnings.push(args.join(' ')),
    });

    assert.deepEqual(ids, [1]);
    assert.match(warnings[0], /Skipping oversized stored session for workspace 3/);
  });

  it('stored-session listing fails closed when the query breaks', async () => {
    const warnings = [];
    const pool = { async query() { throw new Error('boom'); } };

    const ids = await listStoredSessionWorkspaceIds(pool, {
      warn: (...args) => warnings.push(args.join(' ')),
    });

    assert.deepEqual(ids, []);
    assert.match(warnings[0], /Failed to list stored sessions/);
  });

  it('checks durable workspace enablement before status reconnects', async () => {
    const queries = [];
    const pool = {
      async query(sql, params) {
        queries.push({ sql, params });
        return { rows: [{ enabled: true }] };
      },
    };

    assert.equal(await isStoredSessionEnabled(pool, 42), true);
    assert.equal(queries[0].sql, STORED_SESSION_ENABLED_SQL);
    assert.deepEqual(queries[0].params, [42]);
  });

  it('session enablement fails closed', async () => {
    const warnings = [];
    const pool = { async query() { throw new Error('boom'); } };

    assert.equal(
      await isStoredSessionEnabled(pool, 42, {
        warn: (...args) => warnings.push(args.join(' ')),
      }),
      false,
    );
    assert.match(warnings[0], /Failed to read stored-session enablement/);
  });
});
