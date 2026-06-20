import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { DurableEventOutbox } from './event-outbox.js';

class FakePool {
  constructor({ failSchemaOnce = false } = {}) {
    this.rows = [];
    this.nextId = 1;
    this.schemaCalls = 0;
    this.failSchemaOnce = failSchemaOnce;
  }

  async query(sql, params = []) {
    if (sql.includes('CREATE TABLE')) {
      this.schemaCalls += 1;
      if (this.failSchemaOnce && this.schemaCalls === 1) {
        const err = new Error('the database system is starting up');
        err.code = '57P03';
        throw err;
      }
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
      const [id, attempts, , lastError] = params;
      const row = this.rows.find((item) => item.id === id);
      if (row) {
        row.attempts = attempts;
        row.last_error = lastError;
      }
      return { rows: [] };
    }
    if (sql.includes('COUNT(*)')) {
      const workspaceId = params[0];
      const count = this.rows.filter((row) => !workspaceId || row.workspace_id === workspaceId).length;
      return { rows: [{ count }] };
    }
    throw new Error(`Unexpected SQL: ${sql}`);
  }
}

describe('DurableEventOutbox', () => {
  it('dedupes by idempotency key before forwarding', async () => {
    const pool = new FakePool();
    const forwarded = [];
    const outbox = new DurableEventOutbox(
      pool,
      async (path, payload) => forwarded.push({ path, payload }),
    );

    const event = {
      workspaceId: 7,
      eventType: 'msg.inbound',
      idempotencyKey: 'tg:100:200',
      path: '/api/webhook/telegram',
      payload: { chatId: '100', messageId: 200 },
    };

    await outbox.enqueue(event);
    await outbox.enqueue(event);
    assert.equal(await outbox.pendingCount(7), 1);

    const delivered = await outbox.flush({ workspaceId: 7 });

    assert.equal(delivered, 1);
    assert.deepEqual(forwarded, [{
      path: '/api/webhook/telegram',
      payload: { chatId: '100', messageId: 200 },
    }]);
    assert.equal(await outbox.pendingCount(7), 0);
  });

  it('keeps failed events durable with retry metadata', async () => {
    const pool = new FakePool();
    const outbox = new DurableEventOutbox(pool, async () => {
      throw new Error('backend down');
    });

    await outbox.enqueue({
      workspaceId: 9,
      eventType: 'msg.deleted',
      idempotencyKey: 'tg:100:del:1',
      path: '/api/webhook/telegram/message-delete',
      payload: { chatId: '100', messageIds: [1] },
    });

    const delivered = await outbox.flush({ workspaceId: 9 });

    assert.equal(delivered, 0);
    assert.equal(await outbox.pendingCount(9), 1);
    assert.equal(pool.rows[0].attempts, 1);
    assert.equal(pool.rows[0].last_error, 'backend down');
  });

  it('clears failed schema readiness so startup retries can query again', async () => {
    const pool = new FakePool({ failSchemaOnce: true });
    const outbox = new DurableEventOutbox(pool, async () => {});

    await assert.rejects(
      () => outbox.ensureSchema(),
      /database system is starting up/,
    );
    await outbox.ensureSchema();

    assert.equal(pool.schemaCalls, 2);
  });
});
