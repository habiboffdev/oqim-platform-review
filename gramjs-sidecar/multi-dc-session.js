import { AuthKey } from 'telegram/crypto/AuthKey.js';
import { MemorySession, StringSession } from 'telegram/sessions/index.js';

const SESSION_PREFIX = 'oqim-session-v1:';

function encodePayload(payload) {
  return `${SESSION_PREFIX}${Buffer.from(JSON.stringify(payload), 'utf8').toString('base64')}`;
}

function decodePayload(serialized) {
  if (!serialized || !serialized.startsWith(SESSION_PREFIX)) {
    return null;
  }

  const encoded = serialized.slice(SESSION_PREFIX.length);
  return JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
}

function serializeAuthKey(authKey) {
  const key = authKey?.getKey?.();
  if (!Buffer.isBuffer(key) || !key.length) {
    return null;
  }
  return key.toString('base64');
}

async function deserializeAuthKey(encoded) {
  if (!encoded) {
    return undefined;
  }
  const authKey = new AuthKey();
  await authKey.setKey(Buffer.from(encoded, 'base64'));
  return authKey;
}

export class MultiDcSession extends MemorySession {
  constructor(serialized = '', onSave = null, options = {}) {
    super();
    this._serialized = serialized || '';
    this._onSave = onSave;
    this._authKeys = {};
    this._persistEntityLimit = Math.max(0, Number(options.persistEntityLimit || 0));
  }

  setSaveHandler(onSave = null) {
    this._onSave = onSave;
  }

  get authKey() {
    return this.getAuthKey(this._dcId);
  }

  set authKey(value) {
    this.setAuthKey(value, this._dcId);
  }

  getAuthKey(dcId = this._dcId) {
    if (!dcId) {
      return undefined;
    }
    return this._authKeys[dcId];
  }

  setAuthKey(authKey, dcId = this._dcId) {
    if (!dcId) {
      return undefined;
    }
    if (!authKey) {
      delete this._authKeys[dcId];
      return undefined;
    }
    this._authKeys[dcId] = authKey;
    return undefined;
  }

  async load() {
    if (!this._serialized) {
      return;
    }

    const payload = decodePayload(this._serialized);
    if (payload) {
      this._dcId = payload.mainDcId || 0;
      this._serverAddress = payload.serverAddress || undefined;
      this._port = payload.port || undefined;
      this._authKeys = {};
      this._entities = new Set(
        (payload.entities || [])
          .filter(Array.isArray)
          .slice(0, this._persistEntityLimit),
      );
      for (const [dcId, encoded] of Object.entries(payload.authKeys || {})) {
        const authKey = await deserializeAuthKey(encoded);
        if (authKey) {
          this._authKeys[Number(dcId)] = authKey;
        }
      }
      return;
    }

    const legacy = new StringSession(this._serialized);
    await legacy.load();
    this._dcId = legacy.dcId;
    this._serverAddress = legacy.serverAddress;
    this._port = legacy.port;
    this._authKeys = {};
    this._entities = new Set();
    const authKey = legacy.authKey;
    if (authKey) {
      this._authKeys[this._dcId] = authKey;
    }
  }

  async save() {
    const authKeys = {};
    for (const [dcId, authKey] of Object.entries(this._authKeys)) {
      const encoded = serializeAuthKey(authKey);
      if (encoded) {
        authKeys[dcId] = encoded;
      }
    }
    const serialized = encodePayload({
      mainDcId: this._dcId,
      serverAddress: this._serverAddress || '',
      port: this._port || 0,
      authKeys,
      entities: [],
    });
    this._serialized = serialized;
    if (this._onSave) {
      await this._onSave(serialized);
    }
    return serialized;
  }

  async delete() {
    this._serialized = '';
    this._authKeys = {};
    if (this._onSave) {
      await this._onSave('');
    }
  }
}

export function isMultiDcSessionPayload(value) {
  return Boolean(decodePayload(value));
}
