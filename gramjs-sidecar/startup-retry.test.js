import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  isTransientDbStartupError,
  withStartupRetry,
} from './startup-retry.js';

test('startup retry treats Postgres starting-up errors as transient', () => {
  assert.equal(
    isTransientDbStartupError({
      code: '57P03',
      message: 'the database system is starting up',
    }),
    true,
  );
  assert.equal(
    isTransientDbStartupError({
      code: '28P01',
      message: 'password authentication failed',
    }),
    false,
  );
});

test('startup retry retries transient database startup failures', async () => {
  let attempts = 0;
  const delays = [];
  let now = 0;

  const result = await withStartupRetry(
    'test database',
    async () => {
      attempts += 1;
      if (attempts < 3) {
        const err = new Error('the database system is starting up');
        err.code = '57P03';
        throw err;
      }
      return 'ready';
    },
    {
      timeoutMs: 10_000,
      initialDelayMs: 100,
      maxDelayMs: 500,
      nowFn: () => now,
      sleepFn: async (delayMs) => {
        delays.push(delayMs);
        now += delayMs;
      },
    },
  );

  assert.equal(result, 'ready');
  assert.equal(attempts, 3);
  assert.deepEqual(delays, [100, 170]);
});

test('startup retry does not hide non-transient database failures', async () => {
  let attempts = 0;

  await assert.rejects(
    () => withStartupRetry(
      'test database',
      async () => {
        attempts += 1;
        const err = new Error('password authentication failed');
        err.code = '28P01';
        throw err;
      },
      {
        sleepFn: async () => {
          throw new Error('should not sleep');
        },
      },
    ),
    /password authentication failed/,
  );

  assert.equal(attempts, 1);
});
