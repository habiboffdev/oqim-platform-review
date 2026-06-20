import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { AuthKey } from 'telegram/crypto/AuthKey.js';
import { StringSession } from 'telegram/sessions/index.js';

import { MultiDcSession } from './multi-dc-session.js';

async function buildAuthKey(seed) {
  const authKey = new AuthKey();
  await authKey.setKey(Buffer.alloc(256, seed));
  return authKey;
}

describe('MultiDcSession', () => {
  it('loads a legacy StringSession payload into the primary dc auth key', async () => {
    const legacy = new StringSession('');
    legacy.setDC(4, '149.154.167.91', 443);
    legacy.authKey = await buildAuthKey(7);

    const session = new MultiDcSession(legacy.save());
    await session.load();

    assert.equal(session.dcId, 4);
    assert.equal(session.serverAddress, '149.154.167.91');
    assert.equal(session.port, 443);
    assert.deepEqual(
      session.getAuthKey(4)?.getKey(),
      legacy.authKey?.getKey(),
    );
  });

  it('round-trips multiple dc auth keys without persisting entity cache', async () => {
    const session = new MultiDcSession('');
    session.setDC(4, '149.154.167.91', 443);
    session.setAuthKey(await buildAuthKey(1), 4);
    session.setAuthKey(await buildAuthKey(2), 2);
    session._entities.add(['123', '456', 'muxlisa', '+99890', 'Operator']);

    const serialized = await session.save();

    const loaded = new MultiDcSession(serialized);
    await loaded.load();

    assert.equal(loaded.dcId, 4);
    assert.deepEqual(
      loaded.getAuthKey(4)?.getKey(),
      session.getAuthKey(4)?.getKey(),
    );
    assert.deepEqual(
      loaded.getAuthKey(2)?.getKey(),
      session.getAuthKey(2)?.getKey(),
    );
    assert.deepEqual([...loaded._entities], []);
    assert(!serialized.includes('muxlisa'));
  });

  it('persists through the onSave callback', async () => {
    const saved = [];
    const session = new MultiDcSession('', async (serialized) => {
      saved.push(serialized);
    });
    session.setDC(4, '149.154.167.91', 443);
    session.setAuthKey(await buildAuthKey(3), 4);

    const serialized = await session.save();

    assert.equal(saved.length, 1);
    assert.equal(saved[0], serialized);
    assert.match(serialized, /^oqim-session-v1:/);
  });
});
