import crypto from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

import { MultiDcSession } from './multi-dc-session.js';

export const DEFAULT_MAX_SESSION_BYTES = 10 * 1024 * 1024;

export function createSessionStore({
  pool,
  sessionKey = '',
  sessionFile = './session.txt',
  bootstrapKey = '__bootstrap__',
  maxSessionBytes = DEFAULT_MAX_SESSION_BYTES,
} = {}) {
  function byteLength(text) {
    return Buffer.byteLength(String(text || ''), 'utf8');
  }

  function encrypt(text) {
    if (!sessionKey) return text;
    const iv = crypto.randomBytes(16);
    const key = Buffer.from(sessionKey, 'base64').subarray(0, 32);
    const cipher = crypto.createCipheriv('aes-256-cbc', key, iv);
    let encrypted = cipher.update(text, 'utf8', 'base64');
    encrypted += cipher.final('base64');
    return `${iv.toString('base64')}:${encrypted}`;
  }

  function decrypt(data) {
    if (!sessionKey) return data;
    const [ivB64, encrypted] = data.split(':');
    if (!encrypted) return data;
    const iv = Buffer.from(ivB64, 'base64');
    const key = Buffer.from(sessionKey, 'base64').subarray(0, 32);
    const decipher = crypto.createDecipheriv('aes-256-cbc', key, iv);
    let decrypted = decipher.update(encrypted, 'base64', 'utf8');
    decrypted += decipher.final('utf8');
    return decrypted;
  }

  async function loadSessionRecord(workspaceId) {
    if (workspaceId) {
      try {
        const res = await pool.query(
          `SELECT
             CASE WHEN octet_length(session_data) <= $2 THEN session_data ELSE NULL END AS session_data,
             octet_length(session_data) AS session_bytes,
             transport,
             client_profile
           FROM telegram_sessions
           WHERE workspace_id = $1`,
          [workspaceId, maxSessionBytes],
        );
        if (res.rows.length > 0 && Number(res.rows[0].session_bytes || 0) > maxSessionBytes) {
          console.warn(
            `[Session] Skipping oversized session for workspace ${workspaceId}: `
              + `${res.rows[0].session_bytes} bytes exceeds ${maxSessionBytes}`,
          );
          return { sessionString: '', transport: null, clientProfile: null };
        }
        if (res.rows.length > 0 && res.rows[0].session_data) {
          return {
            sessionString: decrypt(res.rows[0].session_data),
            transport: res.rows[0].transport || null,
            clientProfile: res.rows[0].client_profile || null,
          };
        }
      } catch (err) {
        console.warn(`[Session] DB load failed for workspace ${workspaceId}:`, err.message);
      }
      return { sessionString: '', transport: null, clientProfile: null };
    }

    try {
      if (existsSync(sessionFile)) {
        return {
          sessionString: readFileSync(sessionFile, 'utf8').trim(),
          transport: null,
          clientProfile: null,
        };
      }
    } catch {}
    return { sessionString: '', transport: null, clientProfile: null };
  }

  async function loadSessionString(workspaceId) {
    const record = await loadSessionRecord(workspaceId);
    return record.sessionString;
  }

  async function saveSessionString(workspaceId, sessionString, metadata = {}) {
    if (!workspaceId) {
      try {
        writeFileSync(sessionFile, sessionString);
      } catch {}
      return;
    }

    try {
      if (byteLength(sessionString) > maxSessionBytes) {
        console.warn(
          `[Session] Refusing to save oversized session for workspace ${workspaceId}: `
            + `${byteLength(sessionString)} bytes exceeds ${maxSessionBytes}`,
        );
        return;
      }
      const encrypted = encrypt(sessionString);
      if (byteLength(encrypted) > maxSessionBytes) {
        console.warn(
          `[Session] Refusing to save oversized encrypted session for workspace ${workspaceId}: `
            + `${byteLength(encrypted)} bytes exceeds ${maxSessionBytes}`,
        );
        return;
      }
      await pool.query(
        `INSERT INTO telegram_sessions (
           workspace_id, session_data, transport, client_profile, created_at, updated_at
         )
         VALUES ($1, $2, $3, $4, NOW(), NOW())
         ON CONFLICT (workspace_id) DO UPDATE SET
           session_data = $2,
           transport = COALESCE($3, telegram_sessions.transport),
           client_profile = COALESCE($4, telegram_sessions.client_profile),
           updated_at = NOW()`,
        [
          workspaceId,
          encrypted,
          metadata.transport || null,
          metadata.clientProfile || null,
        ],
      );
    } catch (err) {
      console.warn(`[Session] DB save failed for workspace ${workspaceId}:`, err.message);
    }
  }

  function persistenceTargetForRuntime(runtime) {
    if (runtime.workspaceId) {
      return runtime.workspaceId;
    }
    if (runtime.key === bootstrapKey) {
      return null;
    }
    return undefined;
  }

  function buildSessionSaveHandler(persistTarget = undefined) {
    if (persistTarget === undefined) {
      return null;
    }
    return async (serialized) => {
      await saveSessionString(persistTarget, serialized);
    };
  }

  function createSession(sessionString, persistTarget = undefined) {
    const onSave = buildSessionSaveHandler(persistTarget);
    return new MultiDcSession(sessionString, onSave);
  }

  async function snapshotSession(client) {
    const serialized = await client.session.save();
    return typeof serialized === 'string' ? serialized : String(serialized || '');
  }

  function retargetRuntimeSession(runtime, persistTarget) {
    if (runtime.client?.session instanceof MultiDcSession) {
      runtime.client.session.setSaveHandler(buildSessionSaveHandler(persistTarget));
    }
  }

  return {
    buildSessionSaveHandler,
    createSession,
    decrypt,
    encrypt,
    loadSessionRecord,
    loadSessionString,
    persistenceTargetForRuntime,
    retargetRuntimeSession,
    saveSessionString,
    snapshotSession,
  };
}
