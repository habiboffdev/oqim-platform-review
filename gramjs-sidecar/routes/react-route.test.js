import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { createSendIdempotencyCache } from '../send-idempotency-cache.js';
import { createReactRouteHandler } from './react-route.js';

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
  return createReactRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async () => true,
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: (res, err, fallback) => {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: err.message || fallback }));
    },
    reactIdempotencyCache: createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 }),
    ...overrides,
  });
}

test('react route validates required fields', async () => {
  const handler = makeHandler();
  const res = makeResponse();

  await handler(makeJsonRequest({ workspaceId: 7 }), res, new URL('http://localhost/react'));

  assert.equal(res.statusCode, 400);
  assert.deepEqual(res.body, { error: 'messageId must be a positive integer' });
});

test('react route sends reaction once and replays idempotent result', async () => {
  let invokes = 0;
  let request = null;
  const cache = createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 });
  const handler = makeHandler({
    reactIdempotencyCache: cache,
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async (gramjsRequest) => {
          invokes += 1;
          request = gramjsRequest;
          return true;
        },
      },
    }),
  });

  const payload = {
    workspaceId: 7,
    chatId: 123,
    messageId: 1440,
    reaction: '👍',
    idempotencyKey: 'react-k1',
  };
  const first = makeResponse();
  await handler(makeJsonRequest(payload), first, new URL('http://localhost/react'));

  const replay = makeResponse();
  await handler(makeJsonRequest(payload), replay, new URL('http://localhost/react'));

  assert.equal(invokes, 1);
  assert.equal(request.className, 'messages.SendReaction');
  assert.equal(request.msgId, 1440);
  assert.equal(request.reaction[0].className, 'ReactionEmoji');
  assert.equal(request.reaction[0].emoticon, '👍');
  assert.deepEqual(first.body, {
    externalMessageId: 1440,
    chatId: '123',
    reaction: '👍',
  });
  assert.deepEqual(replay.body, {
    externalMessageId: 1440,
    chatId: '123',
    reaction: '👍',
    idempotentReplay: true,
  });
});

test('react route resolves chatId before mutating Telegram message', async () => {
  const resolvedPeer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  let resolveArgs = null;
  let request = null;
  const handler = makeHandler({
    resolvePeer: async (runtime, chatId, options) => {
      resolveArgs = { runtime, chatId, options };
      return resolvedPeer;
    },
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async (gramjsRequest) => {
          request = gramjsRequest;
          return true;
        },
      },
    }),
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({
      workspaceId: 7,
      chatId: 123,
      messageId: 42,
      reaction: '👌',
      idempotencyKey: 'react-resolved',
    }),
    res,
    new URL('http://localhost/react'),
  );

  assert.equal(resolveArgs.chatId, 123);
  assert.deepEqual(resolveArgs.options, { workspaceId: 7, purpose: 'react' });
  assert.equal(request.peer, resolvedPeer);
  assert.deepEqual(res.body, {
    externalMessageId: 42,
    chatId: '123',
    reaction: '👌',
  });
});
