import assert from 'node:assert/strict';
import test from 'node:test';

import { Api } from 'telegram';

import {
  buildInputPeerFromRef,
  createTelegramPeerResolver,
} from './telegram-peer-resolver.js';

test('buildInputPeerFromRef restores a private user input peer with access hash', () => {
  const peer = buildInputPeerFromRef({
    className: 'InputPeerUser',
    userId: '5924086090',
    accessHash: '-3472769761531602767',
  });

  assert.equal(peer instanceof Api.InputPeerUser, true);
  assert.equal(String(peer.userId), '5924086090');
  assert.equal(String(peer.accessHash), '-3472769761531602767');
});

test('peer resolver prefers durable input peer over raw chat id after restart', async () => {
  let durableLookup = null;
  let fallbackCalled = false;
  const resolver = createTelegramPeerResolver({
    durableStateStore: {
      findInputPeerRef: async (workspaceId, chatId) => {
        durableLookup = { workspaceId, chatId };
        return {
          className: 'InputPeerUser',
          userId: '5924086090',
          accessHash: '-3472769761531602767',
        };
      },
    },
    withRpcTimeout: (promise) => promise,
  });
  const runtime = {
    workspaceId: 1,
    client: {
      getInputEntity: async () => {
        fallbackCalled = true;
        throw new Error('should not use network fallback');
      },
    },
  };

  const peer = await resolver.resolve(runtime, '5924086090', { purpose: 'send' });

  assert.deepEqual(durableLookup, { workspaceId: 1, chatId: '5924086090' });
  assert.equal(fallbackCalled, false);
  assert.equal(peer instanceof Api.InputPeerUser, true);
  assert.equal(String(peer.userId), '5924086090');
  assert.equal(String(peer.accessHash), '-3472769761531602767');
});
