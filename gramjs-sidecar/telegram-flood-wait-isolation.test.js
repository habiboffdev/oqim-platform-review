import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { DurableEventOutbox } from './event-outbox.js';
import { buildHotInboundEvent } from './telegram-hot-path.js';
import {
  TELEGRAM_QUEUE_PAUSED,
  runQueuedTelegramMethod,
  telegramMethodQueueStatus,
} from './telegram-method-queue.js';

class FakeOutboxPool {
  constructor() {
    this.rows = [];
    this.nextId = 1;
  }

  async query(sql, params = []) {
    if (sql.includes('CREATE TABLE')) {
      return { rows: [] };
    }
    if (sql.startsWith('INSERT INTO telegram_sidecar_event_outbox')) {
      const [workspaceId, eventType, idempotencyKey, path, payload] = params;
      if (!this.rows.some((row) => row.idempotency_key === idempotencyKey)) {
        this.rows.push({
          id: this.nextId++,
          workspace_id: workspaceId,
          event_type: eventType,
          idempotency_key: idempotencyKey,
          path,
          payload: JSON.parse(payload),
          attempts: 0,
        });
      }
      return { rows: [] };
    }
    if (sql.includes('RETURNING outbox.id')) {
      const [workspaceId, limit] = params;
      return {
        rows: this.rows
          .filter((row) => !workspaceId || row.workspace_id === workspaceId)
          .slice(0, limit)
          .map((row) => ({
            id: row.id,
            path: row.path,
            payload: row.payload,
            attempts: row.attempts,
          })),
      };
    }
    if (sql.startsWith('DELETE FROM telegram_sidecar_event_outbox')) {
      const [id] = params;
      this.rows = this.rows.filter((row) => row.id !== id);
      return { rows: [] };
    }
    if (sql.startsWith('UPDATE telegram_sidecar_event_outbox')) {
      return { rows: [] };
    }
    if (sql.includes('COUNT(*)')) {
      const workspaceId = params[0];
      const count = this.rows.filter(
        (row) => !workspaceId || row.workspace_id === workspaceId,
      ).length;
      return { rows: [{ count }] };
    }
    throw new Error(`Unexpected SQL: ${sql}`);
  }
}

describe('telegram flood-wait isolation', () => {
  it('keeps another workspace live-trigger path moving while background sync is paused', async () => {
    const floodedRuntime = { workspaceId: 1 };
    const liveRuntime = { workspaceId: 2, latestMe: { id: 900 } };
    const forwarded = [];
    const outbox = new DurableEventOutbox(
      new FakeOutboxPool(),
      async (path, payload) => forwarded.push({ path, payload }),
    );

    await assert.rejects(
      () => runQueuedTelegramMethod(
        floodedRuntime,
        { methodClass: 'dialog_sync', label: 'GET_DIALOGS_1', priority: 3 },
        async () => {
          throw Object.assign(new Error('FLOOD_WAIT_31'), { seconds: 31 });
        },
      ),
      /FLOOD_WAIT_31/,
    );
    await assert.rejects(
      () => runQueuedTelegramMethod(
        floodedRuntime,
        { methodClass: 'dialog_sync', label: 'GET_DIALOGS_1', priority: 3 },
        async () => 'should not run while paused',
      ),
      (err) => err.code === TELEGRAM_QUEUE_PAUSED && err.methodClass === 'dialog_sync',
    );

    const liveEvent = buildHotInboundEvent({
      runtime: liveRuntime,
      msg: {
        id: 77,
        isPrivate: true,
        chatId: 222,
        senderId: 333,
        message: 'salom',
        date: 1_700_000_077,
        out: false,
        sender: { id: 333, firstName: 'Ali', bot: false, self: false },
        getChat: async () => {
          throw new Error('live trigger path must not fetch chat');
        },
        getSender: async () => {
          throw new Error('live trigger path must not fetch sender');
        },
      },
      telegramUpdateReceivedAt: 500,
      telegramStateAppliedAt: 501,
      hotEventBuiltAt: 502,
    });
    liveEvent.payload.outbox_enqueued_at = 503;

    await outbox.enqueue(liveEvent);
    const delivered = await outbox.flush({ workspaceId: liveRuntime.workspaceId, limit: 25 });

    assert.equal(delivered, 1);
    assert.deepEqual(forwarded, [{
      path: '/api/webhook/telegram',
      payload: {
        ...liveEvent.payload,
        outbox_enqueued_at: 503,
      },
    }]);
    assert.equal(telegramMethodQueueStatus(floodedRuntime)[0].methodClass, 'dialog_sync');
    assert.deepEqual(telegramMethodQueueStatus(liveRuntime), []);
  });
});
