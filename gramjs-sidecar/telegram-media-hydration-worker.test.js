import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { hydratePendingMediaRefs } from './telegram-media-hydration-worker.js';

const pendingRef = {
  workspaceId: 7,
  chatId: '444',
  messageId: '55',
  mediaKey: 'document:999',
  mediaKind: 'MessageMediaDocument',
  documentId: '999',
  photoId: null,
  mimeType: 'image/png',
  size: 2048,
  status: 'pending',
};

describe('telegram media hydration worker', () => {
  it('hydrates pending media refs through the media fetch lane', async () => {
    const calls = [];
    const peer = { className: 'InputPeerUser', userId: '444', accessHash: '999' };
    const runtime = {
      workspaceId: 7,
      connectionState: 'connected',
      client: {
        getInputEntity: async () => { throw new Error('live client should not resolve media peers'); },
      },
    };

    const result = await hydratePendingMediaRefs({
      runtime,
      durableStateStore: {
        listPendingMediaRefs: async (workspaceId, { limit }) => {
          assert.equal(workspaceId, 7);
          assert.equal(limit, 5);
          return [pendingRef];
        },
        markMediaRefHydrated: async (ref, payload) => {
          calls.push({ type: 'mark_hydrated', ref, payload });
        },
      },
      runQueuedTelegramMethod: async (_runtime, meta, fn) => {
        calls.push({ type: 'queued', meta });
        return fn();
      },
      withRpcTimeout: async (promise, label) => {
        calls.push({ type: 'rpc', label });
        return promise;
      },
      withIsolatedMediaClient: async (_runtime, fn) => fn({
        getInputEntity: async () => { throw new Error('isolated media client should not resolve raw media peers'); },
        getMessages: async (peer, options) => {
          calls.push({ type: 'get_messages', peer, options });
          return [{ id: 55, media: true, document: { mimeType: 'image/png' } }];
        },
        downloadMedia: async (message) => {
          calls.push({ type: 'download', messageId: message.id });
          return Buffer.from('ok');
        },
      }),
      onHydratedMediaRef: async (ref, payload) => {
        calls.push({
          type: 'sink',
          ref,
          payload: {
            mediaType: payload.mediaType,
            contentLength: payload.content.length,
          },
        });
      },
      resolvePeer: async (resolveRuntime, chatId, options) => {
        calls.push({
          type: 'resolve_peer',
          chatId,
          options,
          hasMediaClient: !!resolveRuntime.client?.getMessages,
        });
        return peer;
      },
      limit: 5,
    });

    assert.deepEqual(result, { scanned: 1, hydrated: 1, failed: 0, paused: 0 });
    assert.deepEqual(calls[0], {
      type: 'queued',
      meta: {
        methodClass: 'media_fetch',
        label: 'HYDRATE_MEDIA_7_document:999',
        jobKind: 'media_hydration',
        jobKey: 'document:999',
        priority: 4,
        cursor: {
          chatId: '444',
          messageId: '55',
          mediaKey: 'document:999',
        },
      },
    });
    assert.deepEqual(calls.find((call) => call.type === 'sink').payload, {
      mediaType: 'image/png',
      contentLength: 2,
    });
    assert.deepEqual(calls.find((call) => call.type === 'resolve_peer'), {
      type: 'resolve_peer',
      chatId: '444',
      options: { workspaceId: 7, purpose: 'media_hydration' },
      hasMediaClient: true,
    });
    assert.deepEqual(calls.find((call) => call.type === 'get_messages'), {
      type: 'get_messages',
      peer,
      options: { ids: [55] },
    });
    assert.equal(calls.at(-1).type, 'mark_hydrated');
  });

  it('marks media refs failed when the media object cannot be downloaded', async () => {
    const calls = [];
    const runtime = {
      workspaceId: 7,
      connectionState: 'connected',
      client: {
        getInputEntity: async (chatId) => ({ chatId }),
      },
    };

    const result = await hydratePendingMediaRefs({
      runtime,
      durableStateStore: {
        listPendingMediaRefs: async () => [pendingRef],
        markMediaRefFailed: async (ref, err) => {
          calls.push({ type: 'mark_failed', ref, error: err.message });
        },
      },
      runQueuedTelegramMethod: async (_runtime, _meta, fn) => fn(),
      withRpcTimeout: async (promise) => promise,
      withIsolatedMediaClient: async (_runtime, fn) => fn({
        getMessages: async () => [{ id: 55, media: null }],
      }),
      onHydratedMediaRef: async () => {},
    });

    assert.deepEqual(result, { scanned: 1, hydrated: 0, failed: 1, paused: 0 });
    assert.deepEqual(calls, [{
      type: 'mark_failed',
      ref: pendingRef,
      error: 'MEDIA_REF_NOT_FOUND',
    }]);
  });

  it('leaves media refs pending when the media lane is paused', async () => {
    const err = Object.assign(new Error('TELEGRAM_QUEUE_PAUSED:media_fetch:11'), {
      code: 'TELEGRAM_QUEUE_PAUSED',
      retryAfter: 11,
    });
    const result = await hydratePendingMediaRefs({
      runtime: {
        workspaceId: 7,
        connectionState: 'connected',
        client: { getInputEntity: async () => ({}) },
      },
      durableStateStore: {
        listPendingMediaRefs: async () => [pendingRef],
        markMediaRefFailed: async () => {
          throw new Error('should not mark paused refs failed');
        },
      },
      runQueuedTelegramMethod: async () => {
        throw err;
      },
      withIsolatedMediaClient: async () => {},
      onHydratedMediaRef: async () => {},
    });

    assert.deepEqual(result, { scanned: 1, hydrated: 0, failed: 0, paused: 1 });
  });

  it('backs off recently failed media refs instead of retrying on every startup', async () => {
    const result = await hydratePendingMediaRefs({
      runtime: {
        workspaceId: 7,
        connectionState: 'connected',
        client: { getInputEntity: async () => ({}) },
      },
      durableStateStore: {
        listPendingMediaRefs: async () => [{
          ...pendingRef,
          status: 'failed',
          attempts: 2,
          updatedAt: 1_000,
        }],
      },
      runQueuedTelegramMethod: async () => {
        throw new Error('should not retry recently failed refs');
      },
      withIsolatedMediaClient: async () => {},
      nowSeconds: 1_010,
    });

    assert.deepEqual(result, { scanned: 0, hydrated: 0, failed: 0, paused: 0 });
  });
});
