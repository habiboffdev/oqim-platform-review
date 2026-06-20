import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  CREATE_DURABLE_TELEGRAM_STATE_SQL,
  createTelegramDurableStateStore,
} from './telegram-durable-state-store.js';

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

describe('telegram durable state store', () => {
  it('creates sidecar state tables outside the compact auth session table', async () => {
    const pool = makePool();
    const store = createTelegramDurableStateStore({ pool });

    await store.ensureSchema();

    assert.match(CREATE_DURABLE_TELEGRAM_STATE_SQL, /telegram_sidecar_peers/);
    assert.match(CREATE_DURABLE_TELEGRAM_STATE_SQL, /telegram_sidecar_dialogs/);
    assert.match(CREATE_DURABLE_TELEGRAM_STATE_SQL, /telegram_sidecar_messages/);
    assert.match(CREATE_DURABLE_TELEGRAM_STATE_SQL, /telegram_sidecar_media_refs/);
    assert.match(CREATE_DURABLE_TELEGRAM_STATE_SQL, /telegram_sidecar_update_cursors/);
    assert.doesNotMatch(CREATE_DURABLE_TELEGRAM_STATE_SQL, /telegram_sessions/);
    assert.equal(pool.calls.length, 1);
  });

  it('persists peer and message records from a hot inbound message', async () => {
    const pool = makePool();
    const store = createTelegramDurableStateStore({ pool });

    await store.rememberHotMessageState({
      runtime: { workspaceId: 7 },
      msg: {
        id: 55,
        chatId: 444,
        senderId: 555,
        date: 1_700_000_000,
        message: 'starter coins narxi qancha',
        out: false,
        sender: { id: 555, firstName: 'Ali', username: 'ali' },
        chat: { id: 444, firstName: 'Ali' },
        media: { className: 'MessageMediaPhoto', photo: { id: '999' } },
      },
      source: 'live',
      receivedAt: 123.4,
      appliedAt: 123.5,
    });

    const sqlText = pool.calls.map((call) => call.sql).join('\n');
    assert.match(sqlText, /telegram_sidecar_peers/);
    assert.match(sqlText, /telegram_sidecar_messages/);
    assert.match(sqlText, /telegram_sidecar_update_cursors/);
    assert.deepEqual(
      pool.calls
        .filter((call) => /telegram_sidecar_messages/.test(call.sql))
        .at(-1).params.slice(0, 6),
      [7, '444', '55', '555', 1_700_000_000, 'starter coins narxi qancha'],
    );
    const cursorInsert = pool.calls.find((call) => (
      /INSERT INTO telegram_sidecar_update_cursors/.test(call.sql)
    ));
    assert.equal(cursorInsert.params[2], '');
  });

  it('persists the resolved peer access_hash when gramjs cache leaves msg.chat/sender empty (#417)', async () => {
    const pool = makePool();
    const store = createTelegramDurableStateStore({ pool });

    // First-contact customer: cold gramjs cache, so msg.chat/msg.sender are
    // empty. Without the resolved-peer fallback NOTHING is persisted and the
    // first reply throws "Could not find the input entity for PeerUser".
    await store.rememberHotMessageState({
      runtime: { workspaceId: 7 },
      msg: {
        id: 60,
        isPrivate: true,
        chatId: 1016256593,
        senderId: 1016256593,
        date: 1_700_000_000,
        message: 'salom',
        out: false,
      },
      source: 'live',
      receivedAt: 200.0,
      resolvedPeer: {
        id: 1016256593,
        firstName: 'Jasur',
        accessHash: '8888888888888888888',
      },
    });

    const peerRow = pool.calls
      .filter((call) => /INSERT INTO telegram_sidecar_peers/.test(call.sql))
      .find((call) => (
        String(call.params[1]) === '1016256593'
        && String(call.params[3]) === '8888888888888888888'
      ));
    assert.ok(peerRow, 'first-contact customer access_hash must be durably persisted');
  });

  it('persists channel update cursors only for channel-like messages', async () => {
    const pool = makePool();
    const store = createTelegramDurableStateStore({ pool });

    await store.rememberUpdateCursor({
      runtime: { workspaceId: 7 },
      msg: {
        id: 56,
        chatId: -100123,
        peerId: { channelId: 123n },
        chat: { id: 123n, title: 'SATStation', broadcast: true },
        date: 1_700_000_111,
        pts: 42,
      },
      receivedAt: 223.4,
      appliedAt: 223.5,
    });

    const cursorInsert = pool.calls.find((call) => (
      /INSERT INTO telegram_sidecar_update_cursors/.test(call.sql)
    ));
    assert.equal(cursorInsert.params[2], '123');
    assert.equal(cursorInsert.params[3], 42);
  });

  it('persists dialog shell state with input peer hints', async () => {
    const pool = makePool();
    const runtime = { workspaceId: 7 };
    const store = createTelegramDurableStateStore({ pool });

    const saved = await store.rememberDialogState({
      runtime,
      dialogs: [
        {
          id: 444,
          type: 'private',
          title: 'Ali',
          unreadCount: 2,
          message: { id: 55, message: 'salom', date: 1_700_000_111, out: false },
          inputEntity: {
            className: 'InputPeerUser',
            userId: 444,
            accessHash: 999n,
          },
        },
      ],
      source: 'dialog_sync',
      syncedAt: 456.7,
    });

    assert.equal(saved, 1);
    const insert = pool.calls.find((call) => /INSERT INTO telegram_sidecar_dialogs/.test(call.sql));
    assert.deepEqual(insert.params.slice(0, 9), [
      7,
      '444',
      'private',
      'Ali',
      2,
      '55',
      'salom',
      1_700_000_111,
      false,
    ]);
    assert.deepEqual(JSON.parse(insert.params[9]), {
      className: 'InputPeerUser',
      userId: '444',
      accessHash: '999',
    });
    assert.equal(runtime.telegramDurableStateCounts.dialogs, 1);
  });

  it('returns dialog input peer hints for command routes', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /FROM telegram_sidecar_dialogs/);
        assert.deepEqual(params, [7, '5924086090']);
        return {
          rows: [{
            input_peer: {
              className: 'InputPeerUser',
              userId: '5924086090',
              accessHash: '-3472769761531602767',
            },
            source: 'dialog_sync',
          }],
        };
      },
    };
    const store = createTelegramDurableStateStore({ pool });

    assert.deepEqual(await store.findInputPeerRef(7, 5924086090), {
      className: 'InputPeerUser',
      userId: '5924086090',
      accessHash: '-3472769761531602767',
      source: 'dialog_sync',
    });
  });

  it('falls back to peer rows when dialog input peer is missing', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.deepEqual(params, [7, '5924086090']);
        if (/FROM telegram_sidecar_dialogs/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /FROM telegram_sidecar_peers/);
        return {
          rows: [{
            peer_id: '5924086090',
            peer_kind: 'user',
            access_hash: '-3472769761531602767',
            flags: {},
            source: 'live',
          }],
        };
      },
    };
    const store = createTelegramDurableStateStore({ pool });

    assert.deepEqual(await store.findInputPeerRef(7, '5924086090'), {
      className: 'InputPeerUser',
      userId: '5924086090',
      accessHash: '-3472769761531602767',
      source: 'live',
    });
  });

  it('queues media refs separately from raw message rows', async () => {
    const pool = makePool();
    const runtime = { workspaceId: 7 };
    const store = createTelegramDurableStateStore({ pool });

    await store.rememberMessage({
      runtime,
      msg: {
        id: 55,
        chatId: 444,
        senderId: 555,
        date: 1_700_000_000,
        message: 'rasm',
        out: false,
        media: {
          className: 'MessageMediaDocument',
          document: {
            id: 999n,
            mimeType: 'image/png',
            size: 2048,
          },
        },
      },
      source: 'live',
      receivedAt: 123.4,
    });

    const mediaInsert = pool.calls.find((call) => (
      /INSERT INTO telegram_sidecar_media_refs/.test(call.sql)
    ));
    assert.deepEqual(mediaInsert.params.slice(0, 10), [
      7,
      '444',
      '55',
      'document:999',
      'MessageMediaDocument',
      '999',
      null,
      'image/png',
      2048,
      'live',
    ]);
    assert.equal(mediaInsert.params[10], 123.4);
    assert.equal(runtime.telegramDurableStateCounts.mediaRefs, 1);
  });

  it('summarizes persisted durable state rows for status', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /telegram_sidecar_dialogs/);
        assert.deepEqual(params, [7]);
        return {
          rows: [{
            peers: '2',
            dialogs: '3',
            messages: '10',
            media_refs: '4',
            cursors: '1',
          }],
        };
      },
    };
    const store = createTelegramDurableStateStore({ pool });

    assert.deepEqual(await store.summaryForWorkspace(7), {
      peers: 2,
      dialogs: 3,
      messages: 10,
      mediaRefs: 4,
      cursors: 1,
    });
  });

  it('reports cursor freshness for operator status', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /FROM telegram_sidecar_update_cursors/);
        assert.deepEqual(params, [7]);
        return {
          rows: [
            {
              cursor_scope: 'hot_path',
              channel_id: '',
              pts: '10',
              seq: '20',
              qts: null,
              telegram_date: '1700000000',
              received_at: 400,
              applied_at: 410,
              degraded_state: { reason: 'none' },
            },
            {
              cursor_scope: 'channel',
              channel_id: '99',
              pts: '12',
              seq: null,
              qts: null,
              telegram_date: '1700000010',
              received_at: 100,
              applied_at: 120,
              degraded_state: { floodWait: 11 },
            },
          ],
        };
      },
    };
    const store = createTelegramDurableStateStore({ pool });

    assert.deepEqual(
      await store.cursorFreshnessForWorkspace(7, { now: 500, staleAfterSeconds: 300 }),
      {
        latestReceivedAt: 400,
        latestAppliedAt: 410,
        maxAgeSeconds: 380,
        stale: true,
        cursors: [
          {
            scope: 'hot_path',
            channelId: '',
            pts: 10,
            seq: 20,
            qts: null,
            telegramDate: 1_700_000_000,
            receivedAt: 400,
            appliedAt: 410,
            ageSeconds: 90,
            stale: false,
            degradedState: { reason: 'none' },
          },
          {
            scope: 'channel',
            channelId: '99',
            pts: 12,
            seq: null,
            qts: null,
            telegramDate: 1_700_000_010,
            receivedAt: 100,
            appliedAt: 120,
            ageSeconds: 380,
            stale: true,
            degradedState: { floodWait: 11 },
          },
        ],
      },
    );
  });

  it('prunes legacy private chat hot-path cursors while preserving channel cursors', async () => {
    let deleteSql = '';
    let deleteParams = null;
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        deleteSql = sql;
        deleteParams = params;
        return { rows: [], rowCount: 4 };
      },
    };
    const store = createTelegramDurableStateStore({ pool });

    assert.equal(await store.pruneLegacyPrivateHotPathCursors(7), 4);
    assert.deepEqual(deleteParams, [7]);
    assert.match(deleteSql, /DELETE FROM telegram_sidecar_update_cursors/);
    assert.match(deleteSql, /cursor_scope = 'hot_path'/);
    assert.match(deleteSql, /peer_kind = 'chat'/);
    assert.match(deleteSql, /broadcast/);
    assert.match(deleteSql, /megagroup/);
  });

  it('persists explicit update cursor state for gap repair', async () => {
    const pool = makePool();
    const runtime = { workspaceId: 7 };
    const store = createTelegramDurableStateStore({ pool });

    await store.rememberUpdateCursorState({
      runtime,
      cursorScope: 'gap_repair',
      channelId: '',
      pts: 15,
      seq: 22,
      qts: 0,
      telegramDate: 1_700_000_100,
      degradedState: { repaired: true },
      receivedAt: 500,
      appliedAt: 501,
    });

    const insert = pool.calls.find((call) => /INSERT INTO telegram_sidecar_update_cursors/.test(call.sql));
    assert.deepEqual(insert.params.slice(0, 10), [
      7,
      'gap_repair',
      '',
      15,
      22,
      0,
      1_700_000_100,
      JSON.stringify({ repaired: true }),
      500,
      501,
    ]);
    assert.equal(runtime.telegramDurableStateCounts.cursors, 1);
  });

  it('lists pending media refs for hydration workers', async () => {
    const pool = {
      async query(sql, params = []) {
        if (/CREATE TABLE/.test(sql)) {
          return { rows: [] };
        }
        assert.match(sql, /FROM telegram_sidecar_media_refs/);
        assert.match(sql, /status IN \('pending', 'failed'\)/);
        assert.deepEqual(params, [7, 2]);
        return {
          rows: [{
            workspace_id: '7',
            chat_id: '444',
            message_id: '55',
            media_key: 'document:999',
            media_kind: 'MessageMediaDocument',
            document_id: '999',
            photo_id: null,
            mime_type: 'image/png',
            size: '2048',
            source: 'live',
            status: 'pending',
            attempts: '0',
            last_error: null,
            queued_at: 123.4,
            hydrated_at: null,
          }],
        };
      },
    };
    const store = createTelegramDurableStateStore({ pool });

    assert.deepEqual(await store.listPendingMediaRefs(7, { limit: 2 }), [{
      workspaceId: 7,
      chatId: '444',
      messageId: '55',
      mediaKey: 'document:999',
      mediaKind: 'MessageMediaDocument',
      documentId: '999',
      photoId: null,
      mimeType: 'image/png',
      size: 2048,
      source: 'live',
      status: 'pending',
      attempts: 0,
      lastError: null,
      queuedAt: 123.4,
      hydratedAt: null,
    }]);
  });

  it('marks media ref hydration outcomes', async () => {
    const pool = makePool();
    const store = createTelegramDurableStateStore({ pool });
    const ref = {
      workspaceId: 7,
      chatId: '444',
      messageId: '55',
      mediaKey: 'document:999',
    };

    await store.markMediaRefHydrated(ref, { hydratedAt: 777.1 });
    await store.markMediaRefFailed(ref, new Error('download failed'));

    const hydrated = pool.calls.find((call) => /status = 'hydrated'/.test(call.sql));
    assert.deepEqual(hydrated.params, [777.1, 7, '444', '55', 'document:999']);

    const failed = pool.calls.find((call) => /status = 'failed'/.test(call.sql));
    assert.deepEqual(failed.params, ['download failed', 7, '444', '55', 'document:999']);
  });
});
