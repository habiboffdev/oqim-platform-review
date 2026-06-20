import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import test from 'node:test';

import {
  createHttpAuth,
  json,
  parseBody,
  parseWorkspaceId,
  requireTempSessionId,
  requireWorkspaceId,
} from './http-utils.js';

function responseRecorder() {
  return {
    headers: null,
    headersSent: false,
    status: null,
    body: null,
    destroyed: false,
    writableEnded: false,
    writeHead(status, headers) {
      this.status = status;
      this.headers = headers;
      this.headersSent = true;
    },
    end(body) {
      this.body = body;
      this.writableEnded = true;
    },
  };
}

test('parseBody parses JSON and treats an empty body as an object', async () => {
  assert.deepEqual(await parseBody(Readable.from(['{"workspaceId":42}'])), { workspaceId: 42 });
  assert.deepEqual(await parseBody(Readable.from([])), {});
});

test('parseBody rejects invalid JSON', async () => {
  await assert.rejects(parseBody(Readable.from(['{bad json'])), SyntaxError);
});

test('json writes a stable JSON response', () => {
  const res = responseRecorder();

  assert.equal(json(res, 202, { accepted: true }), true);

  assert.equal(res.status, 202);
  assert.deepEqual(res.headers, { 'Content-Type': 'application/json' });
  assert.equal(res.body, '{"accepted":true}');
});

test('json is a no-op after a response is committed', () => {
  const res = responseRecorder();
  res.headersSent = true;

  assert.equal(json(res, 500, { error: 'late' }), false);

  assert.equal(res.status, null);
  assert.equal(res.body, null);
});

test('createHttpAuth preserves optional sidecar key behavior', () => {
  const openAuth = createHttpAuth('');
  assert.equal(openAuth.isAuthenticatedRequest({ headers: {} }), true);
  assert.equal(openAuth.checkAuth({ headers: {} }, responseRecorder()), true);

  const keyedAuth = createHttpAuth('secret');
  assert.equal(keyedAuth.isAuthenticatedRequest({ headers: {} }), false);
  assert.equal(keyedAuth.isAuthenticatedRequest({ headers: { 'x-sidecar-key': 'secret' } }), true);

  const denied = responseRecorder();
  assert.equal(keyedAuth.checkAuth({ headers: { 'x-sidecar-key': 'wrong' } }, denied), false);
  assert.equal(denied.status, 401);
  assert.equal(denied.body, '{"error":"Unauthorized"}');
});

test('workspace id parsing accepts positive integers only', () => {
  assert.equal(parseWorkspaceId(7), 7);
  assert.equal(parseWorkspaceId('42'), 42);
  assert.equal(parseWorkspaceId(''), null);
  assert.equal(parseWorkspaceId('0'), null);
  assert.equal(parseWorkspaceId('-1'), null);
  assert.equal(parseWorkspaceId('abc'), null);
});

test('requireWorkspaceId prefers body value and writes 400 when missing', () => {
  const url = new URL('http://localhost:3100/send?workspaceId=9');
  assert.equal(requireWorkspaceId(responseRecorder(), { workspaceId: 5 }, url), 5);
  assert.equal(requireWorkspaceId(responseRecorder(), {}, url), 9);

  const missing = responseRecorder();
  assert.equal(requireWorkspaceId(missing, {}, new URL('http://localhost:3100/send')), null);
  assert.equal(missing.status, 400);
  assert.equal(missing.body, '{"error":"workspaceId required"}');
});

test('requireTempSessionId returns string ids and writes 400 when missing', () => {
  assert.equal(requireTempSessionId(responseRecorder(), { tempSessionId: 123 }), '123');

  const missing = responseRecorder();
  assert.equal(requireTempSessionId(missing, {}), null);
  assert.equal(missing.status, 400);
  assert.equal(missing.body, '{"error":"tempSessionId required"}');
});
