import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createChannelPostsRouteHandler,
  createChannelsRouteHandler,
  createDialogsRouteHandler,
  createMessagesRouteHandler,
} from './history-routes.js';
import { createDurableTelegramMethodRunner } from '../telegram-sync-job-store.js';

function makeResponse() {
  return {
    statusCode: null,
    headers: {},
    body: null,
    setHeader(name, value) {
      this.headers[name] = value;
    },
    writeHead(status, headers = {}) {
      this.statusCode = status;
      Object.assign(this.headers, headers);
    },
    end(body) {
      this.body = body ? JSON.parse(body) : null;
    },
  };
}

function errorResponder(res, err, fallback) {
  res.writeHead(502, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: err.message || fallback }));
}

test('messages route validates chatId and returns sorted filtered messages', async () => {
  let call = null;
  let resolveArgs = null;
  const queueCalls = [];
  const resolvedPeer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  const handler = createMessagesRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      workspaceId: 7,
      connectionState: 'connected',
      client: {
        getMessages: async (chatId, options) => {
          call = { chatId, options };
          return [{ id: 4 }, { id: 2 }, { id: 3 }, { id: 1 }];
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    serializeBackfillMessage: (msg) => ({ messageId: msg.id }),
    resolvePeer: async (runtime, chatId, options) => {
      resolveArgs = { runtime, chatId, options };
      return resolvedPeer;
    },
    runQueuedTelegramMethod: async (_runtime, meta, fn) => {
      queueCalls.push(meta);
      return fn();
    },
  });

  const bad = makeResponse();
  await handler(null, bad, new URL('http://localhost/messages?workspaceId=7'));
  assert.equal(bad.statusCode, 400);

  const ok = makeResponse();
  await handler(null, ok, new URL('http://localhost/messages?workspaceId=7&chatId=123&afterId=1&limit=2'));
  assert.equal(resolveArgs.chatId, '123');
  assert.deepEqual(resolveArgs.options, { workspaceId: 7, purpose: 'messages' });
  assert.deepEqual(call, { chatId: resolvedPeer, options: { minId: 1, limit: 2 } });
  assert.deepEqual(queueCalls.at(-1), {
    methodClass: 'history_sync',
    label: 'GET_MESSAGES_7',
    priority: 4,
  });
  assert.deepEqual(ok.body, [{ messageId: 2 }, { messageId: 3 }, { messageId: 4 }]);
});

test('dialogs route serializes private dialogs and syncs shells', async () => {
  let synced = null;
  let rawDialogs = null;
  const queueCalls = [];
  const handler = createDialogsRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        getDialogs: async () => [{ id: 1 }, { id: 2 }],
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    ensureMe: async () => ({ id: 42 }),
    serializeCurrentPrivateDialogs: (dialogs, meId) => dialogs.map((dialog) => ({ chatId: dialog.id, meId })),
    syncDialogShells: async (_runtime, dialogs, sourceDialogs) => {
      synced = dialogs;
      rawDialogs = sourceDialogs;
    },
    runQueuedTelegramMethod: async (_runtime, meta, fn) => {
      queueCalls.push(meta);
      return fn();
    },
  });

  const res = makeResponse();
  await handler(null, res, new URL('http://localhost/dialogs?workspaceId=7'));

  assert.deepEqual(res.body, [{ chatId: 1, meId: 42 }, { chatId: 2, meId: 42 }]);
  assert.deepEqual(synced, res.body);
  assert.deepEqual(rawDialogs, [{ id: 1 }, { id: 2 }]);
  assert.deepEqual(queueCalls.at(-1), {
    methodClass: 'dialog_sync',
    label: 'GET_DIALOGS_7',
    priority: 3,
  });
});

test('channels route sorts own channels first then by name', async () => {
  const queueCalls = [];
  const handler = createChannelsRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        getDialogs: async () => [{ name: 'B' }, { name: 'A' }, { name: 'Own' }],
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    serializeChannelDialog: (dialog) => ({ name: dialog.name, is_own: dialog.name === 'Own' }),
    runQueuedTelegramMethod: async (_runtime, meta, fn) => {
      queueCalls.push(meta);
      return fn();
    },
  });

  const res = makeResponse();
  await handler(null, res, new URL('http://localhost/channels?workspaceId=7'));

  assert.deepEqual(res.body.map((channel) => channel.name), ['Own', 'A', 'B']);
  assert.deepEqual(queueCalls.at(-1), {
    methodClass: 'dialog_sync',
    label: 'GET_CHANNELS_7',
    priority: 3,
  });
});

test('channel posts route validates channelId and sorts posts', async () => {
  let entityInput = null;
  const queueCalls = [];
  const syncJobs = [];
  const runQueuedTelegramMethod = createDurableTelegramMethodRunner({
    syncJobStore: {
      runSyncJob: async (input, fn) => {
        syncJobs.push(input);
        return fn();
      },
    },
    runQueuedTelegramMethod: async (_runtime, meta, fn) => {
      queueCalls.push(meta);
      return fn();
    },
  });
  const handler = createChannelPostsRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      workspaceId: 7,
      connectionState: 'connected',
      client: {
        getEntity: async (channelId) => {
          entityInput = channelId;
          return { id: channelId };
        },
        getMessages: async () => [{ id: 3 }, { id: 1 }, { id: 2, action: {} }],
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    serializeChannelPost: (message) => ({ postId: message.id }),
    runQueuedTelegramMethod,
  });

  const bad = makeResponse();
  await handler(null, bad, new URL('http://localhost/channel-posts?workspaceId=7'));
  assert.equal(bad.statusCode, 400);

  const ok = makeResponse();
  await handler(null, ok, new URL('http://localhost/channel-posts?workspaceId=7&channelId=55'));
  assert.equal(entityInput, 55);
  assert.deepEqual(queueCalls.at(-1), {
    methodClass: 'onboarding_import',
    label: 'GET_CHANNEL_POSTS_7',
    priority: 4,
  });
  assert.equal(syncJobs.at(-1).jobKind, 'onboarding_import');
  assert.equal(syncJobs.at(-1).jobKey, 'GET_CHANNEL_POSTS_7');
  assert.equal(syncJobs.at(-1).methodClass, 'onboarding_import');
  assert.deepEqual(ok.body, [{ postId: 1 }, { postId: 3 }]);
});

test('channel posts route accepts username handles for onboarding source learning', async () => {
  let entityInput = null;
  const handler = createChannelPostsRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        getEntity: async (channelId) => {
          entityInput = channelId;
          return { id: channelId };
        },
        getMessages: async () => [{ id: 8, message: 'Katalog post' }],
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    serializeChannelPost: (message) => ({ postId: message.id, text: message.message }),
  });

  const res = makeResponse();
  await handler(null, res, new URL('http://localhost/channel-posts?workspaceId=7&channelId=@nafis_shop'));

  assert.equal(entityInput, 'nafis_shop');
  assert.deepEqual(res.body, [{ postId: 8, text: 'Katalog post' }]);
});

test('channel posts route filters posts by onboarding date window', async () => {
  const handler = createChannelPostsRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        getEntity: async (channelId) => ({ id: channelId }),
        getMessages: async () => [
          { id: 1, date: Date.parse('2026-04-30T12:00:00.000Z') / 1000, message: 'Old' },
          { id: 2, date: Date.parse('2026-05-10T12:00:00.000Z') / 1000, message: 'Inside' },
          { id: 3, date: Date.parse('2026-05-19T12:00:00.000Z') / 1000, message: 'Future' },
        ],
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    serializeChannelPost: (message) => ({ postId: message.id, date: message.date, text: message.message }),
  });

  const res = makeResponse();
  await handler(
    null,
    res,
    new URL('http://localhost/channel-posts?workspaceId=7&channelId=@satstation&dateFrom=2026-05-01&dateTo=2026-05-18'),
  );

  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, [{ postId: 2, date: Date.parse('2026-05-10T12:00:00.000Z') / 1000, text: 'Inside' }]);
});
