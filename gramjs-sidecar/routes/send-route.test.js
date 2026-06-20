import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { Api } from 'telegram';

import { createSendIdempotencyCache } from '../send-idempotency-cache.js';
import { createSendRouteHandler } from './send-route.js';

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

function makeHandler(overrides = {}) {
  return createSendRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => ({ id: 42, date: 1710000000 }),
        sendFile: async () => ({ id: 84, date: 1710000002 }),
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: (res, err, fallback) => {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: err.message || fallback }));
    },
    sendIdempotencyCache: createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 }),
    fetchOutboundMedia: async (media) => ({
      buffer: Buffer.from('image-bytes'),
      contentType: media.mimeType || 'image/jpeg',
      fileName: media.fileName || 'photo.jpg',
    }),
    ...overrides,
  });
}

test('send route validates required chatId and text or media', async () => {
  const handler = makeHandler();
  const res = makeResponse();

  await handler(makeJsonRequest({ workspaceId: 7 }), res, new URL('http://localhost/send'));

  assert.equal(res.statusCode, 400);
  assert.deepEqual(res.body, { error: 'chatId, text or media.url, and idempotencyKey required' });
});

test('send route sends once and replays idempotent result', async () => {
  let sends = 0;
  const cache = createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 });
  const handler = makeHandler({
    sendIdempotencyCache: cache,
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => {
          sends += 1;
          return { id: 42, date: 1710000000 };
        },
      },
    }),
  });

  const first = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, text: 'salom', idempotencyKey: 'k1' }),
    first,
    new URL('http://localhost/send'),
  );

  const replay = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, text: 'salom', idempotencyKey: 'k1' }),
    replay,
    new URL('http://localhost/send'),
  );

  assert.equal(sends, 1);
  assert.deepEqual(first.body, { externalMessageId: 42, chatId: '123', date: 1710000000 });
  assert.deepEqual(replay.body, {
    externalMessageId: 42,
    chatId: '123',
    date: 1710000000,
    idempotentReplay: true,
  });
});

test('send route passes replyTo to GramJS sendMessage', async () => {
  let call = null;
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async (chatId, payload) => {
          call = { chatId, payload };
          return { id: 43, date: 1710000001 };
        },
      },
    }),
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({
      workspaceId: 7,
      chatId: 123,
      text: 'salom',
      replyToMsgId: 1440,
      idempotencyKey: 'reply-k1',
    }),
    res,
    new URL('http://localhost/send'),
  );

  assert.deepEqual(call, {
    chatId: 123,
    payload: {
      message: 'salom',
      replyTo: 1440,
    },
  });
  assert.deepEqual(res.body, { externalMessageId: 43, chatId: '123', date: 1710000001 });
});

test('send route resolves chatId before sending after restart', async () => {
  const resolvedPeer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  let resolveArgs = null;
  let sendPeer = null;
  const handler = makeHandler({
    resolvePeer: async (runtime, chatId, options) => {
      resolveArgs = { runtime, chatId, options };
      return resolvedPeer;
    },
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async (chatId) => {
          sendPeer = chatId;
          return { id: 44, date: 1710000003 };
        },
      },
    }),
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, text: 'salom', idempotencyKey: 'resolved-k1' }),
    res,
    new URL('http://localhost/send'),
  );

  assert.equal(resolveArgs.chatId, 123);
  assert.deepEqual(resolveArgs.options, { workspaceId: 7, purpose: 'send' });
  assert.equal(sendPeer, resolvedPeer);
  assert.deepEqual(res.body, { externalMessageId: 44, chatId: '123', date: 1710000003 });
});

test('send route sends media once and replays idempotent result', async () => {
  let textSends = 0;
  let mediaSends = 0;
  let mediaCall = null;
  const cache = createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 });
  const handler = makeHandler({
    sendIdempotencyCache: cache,
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => {
          textSends += 1;
          return { id: 42, date: 1710000000 };
        },
        sendFile: async (chatId, payload) => {
          mediaSends += 1;
          mediaCall = { chatId, payload };
          return { id: 84, date: 1710000002 };
        },
      },
    }),
  });

  const request = {
    workspaceId: 7,
    chatId: 123,
    caption: 'Mana shu model rasmi.',
    media: {
      url: 'https://cdn.example.com/catalog/ring.jpg?v=cache-bust',
      mediaType: 'photo',
      mimeType: 'image/jpeg',
      assetId: 'catalog-image-42-main',
    },
    idempotencyKey: 'media-k1',
  };

  const first = makeResponse();
  await handler(makeJsonRequest(request), first, new URL('http://localhost/send'));

  const replay = makeResponse();
  await handler(makeJsonRequest(request), replay, new URL('http://localhost/send'));

  assert.equal(textSends, 0);
  assert.equal(mediaSends, 1);
  assert.deepEqual(mediaCall, {
    chatId: 123,
    payload: {
      file: mediaCall.payload.file,
      caption: 'Mana shu model rasmi.',
      forceDocument: false,
    },
  });
  assert.equal(mediaCall.payload.file.name, 'photo.jpg');
  assert.equal(mediaCall.payload.file.size, Buffer.byteLength('image-bytes'));
  assert.deepEqual(mediaCall.payload.file.buffer, Buffer.from('image-bytes'));
  assert.deepEqual(first.body, {
    externalMessageId: 84,
    chatId: '123',
    date: 1710000002,
    mediaType: 'photo',
  });
  assert.deepEqual(replay.body, {
    externalMessageId: 84,
    chatId: '123',
    date: 1710000002,
    mediaType: 'photo',
    idempotentReplay: true,
  });
});

test('send route downloads query-string photo url before GramJS upload', async () => {
  let fetchedMedia = null;
  let mediaCall = null;
  const handler = makeHandler({
    fetchOutboundMedia: async (media) => {
      fetchedMedia = media;
      return {
        buffer: Buffer.from('png-bytes'),
        contentType: 'image/png',
        fileName: 'satstation-exam-engine.png',
      };
    },
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendFile: async (chatId, payload) => {
          mediaCall = { chatId, payload };
          return { id: 85, date: 1710000004 };
        },
      },
    }),
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({
      workspaceId: 7,
      chatId: 123,
      caption: 'Mana rasmi',
      media: {
        url: 'https://satstation.io/mockups/satstation-exam-engine.png?v=20260513',
        mediaType: 'photo',
        mimeType: 'image/png',
      },
      idempotencyKey: 'query-photo-k1',
    }),
    res,
    new URL('http://localhost/send'),
  );

  assert.equal(
    fetchedMedia.url,
    'https://satstation.io/mockups/satstation-exam-engine.png?v=20260513',
  );
  assert.equal(mediaCall.payload.file.name, 'satstation-exam-engine.png');
  assert.deepEqual(mediaCall.payload.file.buffer, Buffer.from('png-bytes'));
  assert.equal(mediaCall.payload.forceDocument, false);
  assert.deepEqual(res.body, {
    externalMessageId: 85,
    chatId: '123',
    date: 1710000004,
    mediaType: 'photo',
  });
});

test('normalizeOutboundMedia accepts a document pointer (no url)', async () => {
  let sentPayload = null;
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => ({ id: 1, date: 1 }),
        sendFile: async (peer, payload) => { sentPayload = payload; return { id: 99, date: 5 }; },
        getMessages: async () => ([{ media: { document: { id: 7n, accessHash: 3n, fileReference: Buffer.from('ref') } } }]),
      },
    }),
  });
  const request = {
    workspaceId: 7, chatId: 123,
    caption: 'Kutib turing',
    media: { document: { vaultPeer: '-1001234567890', vaultMessageId: 42 }, mediaType: 'video' },
    idempotencyKey: 'doc-k1',
  };
  const res = makeResponse();
  await handler(makeJsonRequest(request), res, new URL('http://localhost/send'));
  assert.notEqual(res.statusCode, 400);  // the document shape is accepted, not rejected as 'media.url required'
});

test('document send fetches the message and sends by InputDocument (no re-upload)', async () => {
  let getArgs = null;
  let sentFile = null;
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => ({ id: 1, date: 1 }),
        getMessages: async (peer, opts) => { getArgs = { peer, opts }; return [{ media: { document: { id: 7n, accessHash: 3n, fileReference: Buffer.from('ref') } } }]; },
        sendFile: async (peer, payload) => { sentFile = { peer, payload }; return { id: 99, date: 5 }; },
      },
    }),
  });
  const request = {
    workspaceId: 7, chatId: 123, caption: 'Mana intro',
    media: { document: { vaultPeer: '-1001234567890', vaultMessageId: 42 }, mediaType: 'video' },
    idempotencyKey: 'doc-k2',
  };
  const res = makeResponse();
  await handler(makeJsonRequest(request), res, new URL('http://localhost/send'));

  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, { externalMessageId: 99, chatId: '123', date: 5, mediaType: 'video' });
  assert.deepEqual(getArgs.opts, { ids: [42] });             // fetched the vault message by id
  assert.equal(sentFile.payload.caption, 'Mana intro');       // agent caption used
  // assert the sent file is an InputDocument (sent by reference) — instanceof verified reliable in this env:
  assert.ok(sentFile.payload.file instanceof Api.InputDocument);
});

test('document send defaults caption to the live channel-post caption', async () => {
  let sentFile = null;
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => ({ id: 1, date: 1 }),
        getMessages: async () => ([{ message: 'Live channel caption 🔹', media: { document: { id: 7n, accessHash: 3n, fileReference: Buffer.from('ref') } } }]),
        sendFile: async (peer, payload) => { sentFile = { peer, payload }; return { id: 99, date: 5 }; },
      },
    }),
  });
  const request = {
    workspaceId: 7, chatId: 123,
    media: { document: { vaultPeer: '-1001234567890', vaultMessageId: 42 }, mediaType: 'video' },
    idempotencyKey: 'doc-live-caption-k1',
  };
  const res = makeResponse();
  await handler(makeJsonRequest(request), res, new URL('http://localhost/send'));

  assert.equal(res.statusCode, 200);
  assert.equal(sentFile.payload.caption, 'Live channel caption 🔹');  // live message caption fills in
});

test('document send: explicit agent caption wins over the live channel caption', async () => {
  let sentFile = null;
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => ({ id: 1, date: 1 }),
        getMessages: async () => ([{ message: 'Live channel caption 🔹', media: { document: { id: 7n, accessHash: 3n, fileReference: Buffer.from('ref') } } }]),
        sendFile: async (peer, payload) => { sentFile = { peer, payload }; return { id: 99, date: 5 }; },
      },
    }),
  });
  const request = {
    workspaceId: 7, chatId: 123, caption: 'Agent override',
    media: { document: { vaultPeer: '-1001234567890', vaultMessageId: 42 }, mediaType: 'video' },
    idempotencyKey: 'doc-override-caption-k1',
  };
  const res = makeResponse();
  await handler(makeJsonRequest(request), res, new URL('http://localhost/send'));

  assert.equal(res.statusCode, 200);
  assert.equal(sentFile.payload.caption, 'Agent override');  // explicit caption beats live channel caption
});

test('document send with a deleted vault message returns a permanent error', async () => {
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => ({ id: 1, date: 1 }),
        getMessages: async () => ([]),  // message gone
        sendFile: async () => { throw new Error('should not be called'); },
      },
    }),
  });
  const request = {
    workspaceId: 7, chatId: 123, caption: 'x',
    media: { document: { vaultPeer: '-1001234567890', vaultMessageId: 42 }, mediaType: 'video' },
    idempotencyKey: 'doc-k3',
  };
  const res = makeResponse();
  await handler(makeJsonRequest(request), res, new URL('http://localhost/send'));
  assert.equal(res.statusCode, 422);
  assert.match(res.body.error, /vault_document_unavailable/);
});

test('send route rejects non-http media urls', async () => {
  const handler = makeHandler();
  const res = makeResponse();

  await handler(
    makeJsonRequest({
      workspaceId: 7,
      chatId: 123,
      media: { url: 'file:///etc/passwd', mediaType: 'photo' },
      idempotencyKey: 'media-bad-url',
    }),
    res,
    new URL('http://localhost/send'),
  );

  assert.equal(res.statusCode, 400);
  assert.deepEqual(res.body, { error: 'media.url must be an http(s) URL' });
});

test('send route replays durable idempotent result without reconnecting runtime', async () => {
  let runtimeCalls = 0;
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => {
      runtimeCalls += 1;
      throw new Error('should not reconnect on durable replay');
    },
    sendIdempotencyCache: {
      get: async () => ({
        response: { externalMessageId: 777, chatId: '123', date: 1710000001 },
        durable: true,
      }),
      rememberPromise: async () => {},
      rememberResult: async () => {},
      forget: async () => {},
    },
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, text: 'salom', idempotencyKey: 'k1' }),
    res,
    new URL('http://localhost/send'),
  );

  assert.equal(runtimeCalls, 0);
  assert.deepEqual(res.body, {
    externalMessageId: 777,
    chatId: '123',
    date: 1710000001,
    idempotentReplay: true,
  });
});

test('send route maps concurrent in-flight rate limit without duplicate send', async () => {
  let sends = 0;
  let rejectSend;
  let resolveRemembered;
  let resolveReplaySawPromise;
  const sendPromise = new Promise((_resolve, reject) => {
    rejectSend = reject;
  });
  const cache = createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 });
  const remembered = new Promise((resolve) => {
    resolveRemembered = resolve;
  });
  const replaySawPromise = new Promise((resolve) => {
    resolveReplaySawPromise = resolve;
  });
  const handler = makeHandler({
    sendIdempotencyCache: {
      get: (...args) => {
        const cached = cache.get(...args);
        if (cached?.promise) {
          resolveReplaySawPromise();
        }
        return cached;
      },
      rememberPromise: async (...args) => {
        cache.rememberPromise(...args);
        resolveRemembered();
      },
      rememberResult: async (...args) => cache.rememberResult(...args),
      forget: async (...args) => cache.forget(...args),
    },
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        sendMessage: async () => {
          sends += 1;
          return sendPromise;
        },
      },
    }),
  });

  const first = makeResponse();
  const replay = makeResponse();
  const firstRequest = handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, text: 'salom', idempotencyKey: 'k-rate' }),
    first,
    new URL('http://localhost/send'),
  );

  await remembered;

  const replayRequest = handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, text: 'salom', idempotencyKey: 'k-rate' }),
    replay,
    new URL('http://localhost/send'),
  );
  await replaySawPromise;
  rejectSend(Object.assign(new Error('FLOOD_WAIT_17'), { seconds: 17 }));

  await firstRequest;
  await replayRequest;

  assert.equal(sends, 1);
  assert.equal(first.statusCode, 429);
  assert.equal(replay.statusCode, 429);
  assert.equal(first.headers['Retry-After'], '17');
  assert.equal(replay.headers['Retry-After'], '17');
  assert.deepEqual(first.body, { error: 'Rate limited', retryAfter: 17 });
  assert.deepEqual(replay.body, { error: 'Rate limited', retryAfter: 17 });
});
