import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';

import { createSessionStore } from './session-store.js';

function createPool(queryImpl) {
  return {
    calls: [],
    async query(sql, params) {
      this.calls.push([sql, params]);
      return queryImpl ? queryImpl(sql, params) : { rows: [] };
    },
  };
}

test('encrypt/decrypt roundtrips workspace session strings', () => {
  const sessionKey = Buffer.alloc(32, 7).toString('base64');
  const store = createSessionStore({
    pool: createPool(),
    sessionKey,
  });

  const encrypted = store.encrypt('telegram-session');

  assert.notEqual(encrypted, 'telegram-session');
  assert.equal(store.decrypt(encrypted), 'telegram-session');
});

test('bootstrap sessions persist to the configured file', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'oqim-sidecar-session-'));
  const sessionFile = join(dir, 'session.txt');
  const store = createSessionStore({
    pool: createPool(),
    sessionFile,
  });

  try {
    await store.saveSessionString(null, 'bootstrap-session');

    assert.equal(await store.loadSessionString(null), 'bootstrap-session');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('workspace sessions load decrypted DB state', async () => {
  const sessionKey = Buffer.alloc(32, 3).toString('base64');
  let encryptedSession = '';
  const pool = createPool(() => ({
    rows: [{
      session_data: encryptedSession,
      transport: 'tcp',
      client_profile: 'webk',
    }],
  }));
  const store = createSessionStore({
    pool,
    sessionKey,
  });
  encryptedSession = store.encrypt('workspace-session');

  assert.equal(await store.loadSessionString(42), 'workspace-session');
  assert.deepEqual(await store.loadSessionRecord(42), {
    sessionString: 'workspace-session',
    transport: 'tcp',
    clientProfile: 'webk',
  });
  assert.deepEqual(pool.calls[0][1], [42, 10 * 1024 * 1024]);
});

test('workspace sessions skip oversized DB state without transferring the blob', async () => {
  const warnings = [];
  const pool = createPool(() => ({
    rows: [{
      session_data: null,
      session_bytes: 450765200,
      transport: 'web',
      client_profile: 'webk',
    }],
  }));
  const store = createSessionStore({
    pool,
    maxSessionBytes: 10 * 1024 * 1024,
  });
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));
  try {
    assert.deepEqual(await store.loadSessionRecord(3), {
      sessionString: '',
      transport: null,
      clientProfile: null,
    });
  } finally {
    console.warn = originalWarn;
  }

  assert.match(pool.calls[0][0], /CASE WHEN octet_length\(session_data\) <= \$2/);
  assert.deepEqual(pool.calls[0][1], [3, 10 * 1024 * 1024]);
  assert.match(warnings[0], /Skipping oversized session for workspace 3/);
});

test('workspace sessions persist transport metadata', async () => {
  const pool = createPool();
  const store = createSessionStore({ pool });

  await store.saveSessionString(42, 'workspace-session', {
    transport: 'tcp',
    clientProfile: 'webk',
  });

  assert.match(pool.calls[0][0], /transport/);
  assert.deepEqual(pool.calls[0][1], [42, 'workspace-session', 'tcp', 'webk']);
});

test('workspace sessions refuse to save oversized payloads', async () => {
  const warnings = [];
  const pool = createPool();
  const store = createSessionStore({
    pool,
    maxSessionBytes: 10,
  });
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));
  try {
    await store.saveSessionString(42, 'x'.repeat(11));
  } finally {
    console.warn = originalWarn;
  }

  assert.equal(pool.calls.length, 0);
  assert.match(warnings[0], /Refusing to save oversized session for workspace 42/);
});

test('runtime persistence target only writes bootstrap and workspace sessions', () => {
  const store = createSessionStore({
    pool: createPool(),
    bootstrapKey: '__bootstrap__',
  });

  assert.equal(store.persistenceTargetForRuntime({ key: '__bootstrap__' }), null);
  assert.equal(store.persistenceTargetForRuntime({ key: 'ws:7', workspaceId: 7 }), 7);
  assert.equal(store.persistenceTargetForRuntime({ key: 'temp:abc' }), undefined);
});
