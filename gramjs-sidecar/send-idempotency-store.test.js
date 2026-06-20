import assert from 'node:assert/strict';
import test from 'node:test';

import { createSendIdempotencyCache } from './send-idempotency-cache.js';
import { createDurableSendIdempotencyStore } from './send-idempotency-store.js';

class FakePool {
  constructor({ failSchemaOnce = false } = {}) {
    this.rows = [];
    this.schemaCalls = 0;
    this.failSchemaOnce = failSchemaOnce;
  }

  async query(sql, params = []) {
    if (sql.includes('CREATE TABLE') || sql.includes('CREATE INDEX')) {
      this.schemaCalls += 1;
      if (this.failSchemaOnce && this.schemaCalls === 1) {
        const err = new Error('the database system is starting up');
        err.code = '57P03';
        throw err;
      }
      return { rows: [] };
    }
    if (sql.includes('INSERT INTO telegram_sidecar_send_idempotency')) {
      const [workspaceId, idempotencyKey, response, ttlSeconds] = params;
      const expiresAt = Date.now() + Number(ttlSeconds) * 1000;
      const existing = this.rows.find(
        (row) => row.workspace_id === workspaceId && row.idempotency_key === idempotencyKey,
      );
      const payload = {
        workspace_id: workspaceId,
        idempotency_key: idempotencyKey,
        response: JSON.parse(response),
        expires_at: expiresAt,
      };
      if (existing) {
        Object.assign(existing, payload);
      } else {
        this.rows.push(payload);
      }
      return { rows: [] };
    }
    if (sql.includes('SELECT response')) {
      const [workspaceId, idempotencyKey] = params;
      return {
        rows: this.rows
          .filter((row) => row.workspace_id === workspaceId && row.idempotency_key === idempotencyKey)
          .filter((row) => row.expires_at > Date.now())
          .map((row) => ({ response: row.response })),
      };
    }
    if (sql.includes('DELETE FROM telegram_sidecar_send_idempotency')) {
      const [workspaceId, idempotencyKey] = params;
      this.rows = this.rows.filter(
        (row) => !(row.workspace_id === workspaceId && row.idempotency_key === idempotencyKey),
      );
      return { rows: [] };
    }
    throw new Error(`Unhandled SQL: ${sql}`);
  }
}

test('durable send idempotency replays result after memory cache restart', async () => {
  const pool = new FakePool();
  const firstStore = createDurableSendIdempotencyStore({
    pool,
    memoryCache: createSendIdempotencyCache(),
  });

  await firstStore.rememberResult(7, 'send-1', {
    externalMessageId: 42,
    chatId: '123',
    date: 1710000000,
  });

  const restartedStore = createDurableSendIdempotencyStore({
    pool,
    memoryCache: createSendIdempotencyCache(),
  });

  assert.deepEqual(await restartedStore.get(7, 'send-1'), {
    response: {
      externalMessageId: 42,
      chatId: '123',
      date: 1710000000,
    },
    durable: true,
  });
});

test('durable send idempotency forget clears memory and database state', async () => {
  const pool = new FakePool();
  const store = createDurableSendIdempotencyStore({
    pool,
    memoryCache: createSendIdempotencyCache(),
  });

  await store.rememberResult(7, 'send-1', { externalMessageId: 42 });
  await store.forget(7, 'send-1');

  assert.equal(await store.get(7, 'send-1'), null);
});

test('durable send idempotency clears failed schema readiness for retries', async () => {
  const pool = new FakePool({ failSchemaOnce: true });
  const store = createDurableSendIdempotencyStore({
    pool,
    memoryCache: createSendIdempotencyCache(),
  });

  await assert.rejects(
    () => store.ensureSchema(),
    /database system is starting up/,
  );
  await store.ensureSchema();

  assert.equal(pool.schemaCalls, 2);
});
