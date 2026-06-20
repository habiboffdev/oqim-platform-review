import assert from 'node:assert/strict';
import test from 'node:test';

import { createSendIdempotencyCache } from './send-idempotency-cache.js';

test('send idempotency cache returns live result and expires old entries', () => {
  let now = 1000;
  const cache = createSendIdempotencyCache({ ttlMs: 50, now: () => now });

  cache.rememberResult(7, 'abc', { externalMessageId: 42 });

  assert.deepEqual(cache.get(7, 'abc').response, { externalMessageId: 42 });

  now = 1051;
  assert.equal(cache.get(7, 'abc'), null);
});

test('send idempotency cache can share an in-flight promise', async () => {
  const cache = createSendIdempotencyCache({ ttlMs: 50, now: () => 1000 });
  const promise = Promise.resolve({ externalMessageId: 99 });

  cache.rememberPromise(7, 'abc', promise);

  assert.equal(cache.get(7, 'abc').promise, promise);
  assert.deepEqual(await cache.get(7, 'abc').promise, { externalMessageId: 99 });
});

test('send idempotency cache ignores missing keys and supports forget', () => {
  const cache = createSendIdempotencyCache({ ttlMs: 50, now: () => 1000 });

  cache.rememberResult(7, '', { externalMessageId: 42 });
  assert.equal(cache.get(7, ''), null);

  cache.rememberResult(7, 'abc', { externalMessageId: 42 });
  cache.forget(7, 'abc');
  assert.equal(cache.get(7, 'abc'), null);
});
