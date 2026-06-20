import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import {
  createCustomEmojiRouteHandler,
  createDownloadMediaRouteHandler,
} from './media-routes.js';

function makeJsonRequest(payload) {
  const req = new EventEmitter();
  req.headers = { 'content-type': 'application/json' };
  process.nextTick(() => {
    req.emit('data', Buffer.from(JSON.stringify(payload)));
    req.emit('end');
  });
  return req;
}

function makeResponse() {
  return {
    statusCode: null,
    headers: {},
    body: null,
    rawBody: null,
    setHeader(name, value) {
      this.headers[name] = value;
    },
    writeHead(status, headers = {}) {
      this.statusCode = status;
      Object.assign(this.headers, headers);
    },
    end(body) {
      this.rawBody = body;
      try {
        this.body = body ? JSON.parse(body) : null;
      } catch {
        this.body = body;
      }
    },
  };
}

function errorResponder(res, err, fallback) {
  res.writeHead(502, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: err.message || fallback }));
}

function makeWriter(bytesWritten = 1) {
  return {
    bytesWritten,
    close() {},
  };
}

test('download media route validates required fields and thumb range conflict', async () => {
  const handler = createDownloadMediaRouteHandler({
    ensureAuthorizedRuntime: async () => ({ connectionState: 'connected', client: {} }),
    withRpcTimeout: (promise) => promise,
    withIsolatedMediaClient: async () => {},
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    responseCommitted: () => false,
    isClientAbortError: () => false,
    listThumbCandidates: () => [],
    createResponseWriter: () => makeWriter(),
    fallbackDownloadMime: () => 'image/jpeg',
    streamMediaRange: async () => false,
  });

  const missing = makeResponse();
  await handler(makeJsonRequest({ workspaceId: 7 }), missing, new URL('http://localhost/download-media'));
  assert.equal(missing.statusCode, 400);

  const conflict = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 1, messageId: 2, thumb: true, byteRange: 'bytes=0-10' }),
    conflict,
    new URL('http://localhost/download-media'),
  );
  assert.equal(conflict.statusCode, 400);
});

test('download media route streams selected media through writer', async () => {
  let resolvedPeer = null;
  let downloaded = false;
  const queueCalls = [];
  const peer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  const handler = createDownloadMediaRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        getInputEntity: async () => { throw new Error('live client should not resolve media peers'); },
      },
    }),
    withRpcTimeout: (promise) => promise,
    withIsolatedMediaClient: async (_runtime, fn) => fn({
      getInputEntity: async (chatId) => {
        throw new Error(`isolated media client should not resolve raw peer ${chatId}`);
      },
      getMessages: async (inputPeer) => {
        resolvedPeer = inputPeer;
        return [{ id: 2, media: true }];
      },
      downloadMedia: async () => {
        downloaded = true;
      },
    }),
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    responseCommitted: () => false,
    isClientAbortError: () => false,
    listThumbCandidates: () => [],
    createResponseWriter: () => makeWriter(5),
    fallbackDownloadMime: () => 'image/jpeg',
    streamMediaRange: async () => false,
    resolvePeer: async (runtime, chatId, options) => {
      assert.equal(chatId, 123);
      assert.deepEqual(options, { workspaceId: 7, purpose: 'media_download' });
      assert.ok(runtime.client.getMessages);
      return peer;
    },
    runQueuedTelegramMethod: async (_runtime, meta, fn) => {
      queueCalls.push(meta);
      return fn();
    },
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, messageId: 2 }),
    res,
    new URL('http://localhost/download-media'),
  );

  assert.deepEqual(resolvedPeer, peer);
  assert.equal(downloaded, true);
  assert.deepEqual(queueCalls.at(-1), {
    methodClass: 'media_fetch',
    label: 'DOWNLOAD_MEDIA_7',
    priority: 4,
  });
  assert.equal(res.body, null);
});

test('custom emoji route validates document id and returns downloaded buffer', async () => {
  const queueCalls = [];
  const handler = createCustomEmojiRouteHandler({
    ensureAuthorizedRuntime: async () => ({ connectionState: 'connected', client: {} }),
    withRpcTimeout: (promise) => promise,
    withIsolatedMediaClient: async (_runtime, fn) => fn({
      invoke: async () => [{ mimeType: 'image/webp' }],
      downloadMedia: async () => Buffer.from('ok'),
    }),
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: errorResponder,
    listThumbCandidates: () => [],
    createResponseWriter: () => makeWriter(0),
    sniffMediaMime: () => 'application/octet-stream',
    runQueuedTelegramMethod: async (_runtime, meta, fn) => {
      queueCalls.push(meta);
      return fn();
    },
  });

  const bad = makeResponse();
  await handler(null, bad, new URL('http://localhost/custom-emoji?workspaceId=7&documentId=abc'));
  assert.equal(bad.statusCode, 400);

  const ok = makeResponse();
  await handler(null, ok, new URL('http://localhost/custom-emoji?workspaceId=7&documentId=123'));
  assert.equal(ok.statusCode, 200);
  assert.deepEqual(queueCalls.at(-1), {
    methodClass: 'media_fetch',
    label: 'GET_CUSTOM_EMOJI_7',
    priority: 4,
  });
  assert.equal(ok.headers['Content-Type'], 'image/webp');
  assert.equal(ok.rawBody.toString(), 'ok');
});
