import assert from 'node:assert/strict';
import test from 'node:test';

import { discardRuntimeAuthorization } from './runtime-auth-cleanup.js';

test('discardRuntimeAuthorization only destroys the local client and never logs out Telegram', async () => {
  const calls = [];
  const runtime = {
    workspaceId: 1,
    client: {
      invoke() {
        throw new Error('should not invoke Telegram RPC');
      },
    },
  };

  const discarded = await discardRuntimeAuthorization({
    runtime,
    reason: 'duplicate auth attempt',
    runtimeLabel: () => 'workspace:1',
    logger: { log: (message) => calls.push(['log', message]) },
    destroyRuntimeClient: async (target) => {
      calls.push(['destroy', target.workspaceId]);
      target.client = null;
    },
  });

  assert.equal(discarded, true);
  assert.equal(runtime.client, null);
  assert.deepEqual(calls, [
    ['log', '[Sidecar] Discarded workspace:1 Telegram login locally: duplicate auth attempt'],
    ['destroy', 1],
  ]);
});

test('discardRuntimeAuthorization no-ops when no client exists', async () => {
  const runtime = { workspaceId: 2, client: null };
  const discarded = await discardRuntimeAuthorization({
    runtime,
    reason: 'nothing to clean up',
    runtimeLabel: () => 'workspace:2',
    destroyRuntimeClient: async () => {
      throw new Error('should not destroy missing client');
    },
  });

  assert.equal(discarded, false);
});
