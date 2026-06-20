/**
 * OQIM GramJS Sidecar — multi-tenant Telegram MTProto adapter.
 *
 * HTTP API:
 *   GET  /health
 *   GET  /status?workspaceId=123
 *   POST /send            { workspaceId, chatId, text? | media?, caption?, idempotencyKey? }
 *   POST /edit            { workspaceId, chatId, messageId, text, idempotencyKey }
 *   POST /react           { workspaceId, chatId, messageId, reaction, idempotencyKey }
 *   POST /typing          { workspaceId, chatId }
 *   POST /read            { workspaceId, chatId, maxId? }
 *   POST /download-media  { workspaceId, chatId, messageId }
 *   GET  /custom-emoji?workspaceId=123&documentId=456
 *   GET  /dialogs?workspaceId=123
 *   POST /auth/send-code        { phoneNumber, tempSessionId? }
 *   POST /auth/sign-in          { tempSessionId, phoneNumber, phoneCodeHash?, phoneCode }
 *   POST /auth/check-password   { tempSessionId, password }
 *   POST /sessions/register     { tempSessionId, workspaceId }
 *   POST /sessions/connect      { workspaceId, sessionString? }
 *   POST /sessions/disconnect   { workspaceId }
 *   GET  /sessions
 *   GET  /sessions/:id/status
 *   POST /disconnect            { workspaceId? }  // legacy alias
 *
 * Inbound Telegram messages are forwarded to:
 *   POST {BACKEND_URL}/api/webhook/telegram
 */

import http from 'node:http';
import { randomUUID } from 'node:crypto';

import pg from 'pg';
import QRCode from 'qrcode';
import { TelegramClient, Api } from 'telegram';
import { computeCheck } from 'telegram/Password.js';

import {
  isPrivateHumanEntity,
  isPrivateHumanDialog,
  serializePrivateHumanDialog,
} from './chat-filters.js';
import {
  isStoredSessionEnabled,
  listRecentlyActiveWorkspaceIds,
  listStoredSessionWorkspaceIds,
  normalizeLazyBootHours,
} from './lazy-boot.js';
import { DurableEventOutbox } from './event-outbox.js';
import {
  createHttpAuth,
  json,
  parseBody,
  parseWorkspaceId,
  requireTempSessionId,
  requireWorkspaceId,
} from './http-utils.js';
import { createSessionStore } from './session-store.js';
import {
  createResponseWriter,
  streamMediaRange,
} from './media-streaming.js';
import { withStartupRetry } from './startup-retry.js';
import {
  runtimeLabel,
  serializeRuntimeStatus,
  summarizeLiveUpdateHealth,
} from './sidecar-status.js';
import {
  isHealthyWorkspaceSession,
  telegramUserIdsMatch,
} from './session-binding-policy.js';
import { createSendIdempotencyCache } from './send-idempotency-cache.js';
import { createDurableSendIdempotencyStore } from './send-idempotency-store.js';
import {
  buildDeletedEvent,
  buildEditedEvent,
  fallbackDownloadMime,
  listThumbCandidates,
  safeNumber,
  serializeBackfillMessage,
  serializeChannelDialog,
  serializeChannelPost,
  sniffMediaMime,
} from './telegram-codec.js';
import { normalizeTelegramAuthError, normalizeTelegramPhoneAuthError, telegramApiError } from './telegram-errors.js';
import {
  applyTelegramClientProfile,
  buildTelegramClientOptions,
  normalizeTelegramTransport,
  resolveTelegramAuthTransport,
} from './telegram-transport.js';
import { createRuntimeRegistry } from './runtime-registry.js';
import { nextIngestAction } from './ingest-liveness.js';
import { startTelegramUpdatePump } from './telegram-update-pump.js';
import { createSendRouteHandler } from './routes/send-route.js';
import { createEditRouteHandler } from './routes/edit-route.js';
import { createReactRouteHandler } from './routes/react-route.js';
import {
  buildWorkspaceRestoreTransports,
  restoreWorkspaceSession,
} from './workspace-session-restore.js';
import { discardRuntimeAuthorization } from './runtime-auth-cleanup.js';
import {
  createOnlineRouteHandler,
  createReadRouteHandler,
  createTypingRouteHandler,
} from './routes/typing-read-routes.js';
import {
  createChannelPostsRouteHandler,
  createChannelsRouteHandler,
  createDialogsRouteHandler,
  createMessagesRouteHandler,
} from './routes/history-routes.js';
import {
  createCustomEmojiRouteHandler,
  createDownloadMediaRouteHandler,
} from './routes/media-routes.js';
import {
  buildHotInboundEvent,
  cachedPrivatePeer,
  nowSeconds,
} from './telegram-hot-path.js';
import {
  shouldPromoteCatchUpMessageToLiveRecovery,
} from './telegram-live-recovery.js';
import {
  applyHotMessageState,
} from './telegram-state-store.js';
import {
  prepareInboundConnectorEvent,
} from './telegram-connector-runtime.js';
import {
  TELEGRAM_QUEUE_PAUSED,
  runQueuedTelegramMethod,
} from './telegram-method-queue.js';
import { createTelegramDurableStateStore } from './telegram-durable-state-store.js';
import { createTelegramPeerResolver } from './telegram-peer-resolver.js';
import { repairTelegramUpdateGap } from './telegram-gap-repair-worker.js';
import { hydratePendingMediaRefs } from './telegram-media-hydration-worker.js';
import { postHydratedMediaRefToBackend } from './telegram-media-hydration-sink.js';
import {
  createDurableTelegramMethodRunner,
  createTelegramSyncJobStore,
} from './telegram-sync-job-store.js';
import { resumeTelegramSyncJobs } from './telegram-sync-job-resume.js';

process.on('uncaughtException', (err) => {
  if (err.message?.includes('resolve()') || err.message?.includes('non-request instance')) {
    console.warn('[GramJS] Ignoring resolve() bug on update:', err.message);
    return;
  }
  console.error('[FATAL]', err);
  process.exit(1);
});

const memorySendIdempotencyCache = createSendIdempotencyCache();

const PORT = parseInt(process.env.SIDECAR_PORT || '3100', 10);
const API_ID = parseInt(process.env.TELEGRAM_API_ID || '0', 10);
const API_HASH = process.env.TELEGRAM_API_HASH || '';
const SESSION_KEY = process.env.TELEGRAM_SESSION_KEY || '';
const BACKEND_URL = process.env.BACKEND_CALLBACK_URL || 'http://localhost:8001';
const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5434/oqim_business';
const APP_ENV = (process.env.APP_ENV || process.env.NODE_ENV || 'development').toLowerCase();
const DEFAULT_DEV_SIDECAR_KEY = APP_ENV === 'development' ? 'dev-sidecar-key' : '';
const SIDECAR_KEY = process.env.SIDECAR_API_KEY || DEFAULT_DEV_SIDECAR_KEY;
const { checkAuth, isAuthenticatedRequest } = createHttpAuth(SIDECAR_KEY);
const MAX_RECONNECT_ATTEMPTS = 50;
const MAX_RECONNECT_DELAY = 60_000;
const RPC_TIMEOUT_MS = 8_000;
const CATCH_UP_TIMEOUT_MS = 30_000;
const DB_STARTUP_TIMEOUT_MS = parseInt(process.env.SIDECAR_DB_STARTUP_TIMEOUT_MS || '60000', 10);
const TELEGRAM_TRANSPORT = normalizeTelegramTransport(process.env.TELEGRAM_TRANSPORT || 'web');
const TELEGRAM_AUTH_TRANSPORT = resolveTelegramAuthTransport(process.env);
const TELEGRAM_AUTH_CLIENT_PROFILE = (
  process.env.TELEGRAM_AUTH_CLIENT_PROFILE || 'webk'
).trim().toLowerCase();
const SIDECAR_ALLOW_READ_RECEIPTS = _envFlag(
  process.env.SIDECAR_ALLOW_READ_RECEIPTS,
  true,
);
const SIDECAR_ALLOW_ONLINE_PRESENCE = _envFlag(
  process.env.SIDECAR_ALLOW_ONLINE_PRESENCE,
  true,
);
const SIDECAR_BACKGROUND_SYNC_ENABLED = _envFlag(
  process.env.SIDECAR_BACKGROUND_SYNC_ENABLED,
  true,
);
const SIDECAR_MEDIA_HYDRATION_ENABLED = _envFlag(
  process.env.SIDECAR_MEDIA_HYDRATION_ENABLED,
  true,
);
const DIALOG_SYNC_INTERVAL_MS = 300_000;
const AUTH_CONNECT_TIMEOUT_MS = parseInt(process.env.TELEGRAM_AUTH_CONNECT_TIMEOUT_MS || '6000', 10);
const AUTH_INITIAL_DCS = (process.env.TELEGRAM_AUTH_INITIAL_DCS || '2,4')
  .split(',')
  .map((value) => parseInt(value.trim(), 10))
  .filter((value) => Number.isInteger(value) && value > 0);
const TELEGRAM_DC_ENDPOINTS = {
  1: { address: '149.154.175.50', port: 443 },
  2: { address: '149.154.167.50', port: 443 },
  3: { address: '149.154.175.100', port: 443 },
  4: { address: '149.154.167.91', port: 443 },
  5: { address: '149.154.171.5', port: 443 },
};

function _envFlag(value, fallback = false) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return fallback;
  }
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function responseCommitted(res) {
  return Boolean(res.headersSent || res.writableEnded || res.destroyed);
}

function isClientAbortError(err) {
  return err?.message === 'CLIENT_ABORTED'
    || err?.code === 'ECONNRESET'
    || err?.code === 'ERR_STREAM_PREMATURE_CLOSE';
}
const LAZY_BOOT_ACTIVE_HOURS = normalizeLazyBootHours(process.env.SIDECAR_LAZY_BOOT_ACTIVE_HOURS);
const NON_FATAL_TIMEOUT_PREFIXES = [
  'GET_DIALOGS_',
  'GET_CHANNELS_',
  'GET_MESSAGES_',
  'GET_UNREAD_MESSAGES_',
  'GET_CHANNEL_POSTS_',
  'GET_CUSTOM_EMOJI_',
  'DOWNLOAD_MEDIA_',
  'EDIT_MESSAGE_',
];
const BOOTSTRAP_KEY = '__bootstrap__';
const SESSION_FILE = './session.txt';

const pool = new pg.Pool({ connectionString: DATABASE_URL });
const sendIdempotencyCache = createDurableSendIdempotencyStore({
  pool,
  memoryCache: memorySendIdempotencyCache,
});
const eventOutbox = new DurableEventOutbox(pool, postWebhookJson);
const sessionStore = createSessionStore({
  pool,
  sessionKey: SESSION_KEY,
  sessionFile: SESSION_FILE,
  bootstrapKey: BOOTSTRAP_KEY,
});
const telegramDurableState = createTelegramDurableStateStore({ pool });
const telegramPeerResolver = createTelegramPeerResolver({
  durableStateStore: telegramDurableState,
  withRpcTimeout,
});
const telegramSyncJobs = createTelegramSyncJobStore({ pool });
const runDurableTelegramMethod = createDurableTelegramMethodRunner({
  syncJobStore: telegramSyncJobs,
  runQueuedTelegramMethod,
});
const runtimeRegistry = createRuntimeRegistry(BOOTSTRAP_KEY);
const handleSendRoute = createSendRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  sendIdempotencyCache,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleEditRoute = createEditRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  editIdempotencyCache: sendIdempotencyCache,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleReactRoute = createReactRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  reactIdempotencyCache: sendIdempotencyCache,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleTypingRoute = createTypingRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleReadRoute = createReadRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  readReceiptsEnabled: SIDECAR_ALLOW_READ_RECEIPTS,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleOnlineRoute = createOnlineRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  onlinePresenceEnabled: SIDECAR_ALLOW_ONLINE_PRESENCE,
});
const handleMessagesRoute = createMessagesRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  serializeBackfillMessage,
  withBackgroundClient: withRuntimeClient,
  runQueuedTelegramMethod: runDurableTelegramMethod,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleDialogsRoute = createDialogsRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  ensureMe,
  serializeCurrentPrivateDialogs,
  syncDialogShells,
  withBackgroundClient: withRuntimeClient,
  runQueuedTelegramMethod: runDurableTelegramMethod,
});
const handleChannelsRoute = createChannelsRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  serializeChannelDialog,
  withBackgroundClient: withRuntimeClient,
  runQueuedTelegramMethod: runDurableTelegramMethod,
});
const handleChannelPostsRoute = createChannelPostsRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  serializeChannelPost,
  withBackgroundClient: withRuntimeClient,
  runQueuedTelegramMethod: runDurableTelegramMethod,
});
const handleDownloadMediaRoute = createDownloadMediaRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  withIsolatedMediaClient,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  responseCommitted,
  isClientAbortError,
  listThumbCandidates,
  createResponseWriter,
  fallbackDownloadMime,
  streamMediaRange,
  runQueuedTelegramMethod: runDurableTelegramMethod,
  resolvePeer: telegramPeerResolver.resolve,
});
const handleCustomEmojiRoute = createCustomEmojiRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  withIsolatedMediaClient,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  listThumbCandidates,
  createResponseWriter,
  sniffMediaMime,
  runQueuedTelegramMethod: runDurableTelegramMethod,
});

async function postWebhookJson(path, payload) {
  const headers = { 'Content-Type': 'application/json' };
  if (SIDECAR_KEY) {
    headers['X-Sidecar-Key'] = SIDECAR_KEY;
  }

  const response = await fetch(`${BACKEND_URL}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Backend returned ${response.status}`);
  }
}


function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withRpcTimeout(promise, label, timeoutMs = RPC_TIMEOUT_MS) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(`${label}_TIMEOUT`)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function markRuntimeRpcFailure(runtime, err) {
  runtime.lastError = err.message;
  const message = err.message || '';
  const normalized = normalizeTelegramAuthError(err);
  if (normalized.code === 'SESSION_REVOKED') {
    console.warn(`[GramJS] ${runtimeLabel(runtime)} session revoked during RPC; forcing reconnect`);
    runtime.connectionState = 'disconnected';
    runtime.lastError = normalized.code;
    await destroyRuntimeClient(runtime);
    return;
  }
  const nonFatalTimeout = NON_FATAL_TIMEOUT_PREFIXES.some((prefix) => message.startsWith(prefix));
  if (message.endsWith('_TIMEOUT') && !nonFatalTimeout) {
    console.warn(`[GramJS] ${runtimeLabel(runtime)} RPC timed out; forcing reconnect`);
    runtime.connectionState = 'disconnected';
    await destroyRuntimeClient(runtime);
    scheduleReconnect(runtime);
  }
}

function getBootstrapRuntime() {
  return runtimeRegistry.getBootstrap();
}

function getWorkspaceRuntime(workspaceId) {
  return runtimeRegistry.getWorkspace(workspaceId);
}

function getTempRuntime(tempSessionId) {
  return runtimeRegistry.getTemp(tempSessionId);
}

function generateTempSessionId() {
  return randomUUID();
}

const PREFERRED_LOGIN_DELIVERY_TYPE = 'auth.SentCodeTypeApp';

function serializeSentCode(result, options = {}) {
  const typeName = result?.type?.className || null;
  const nextTypeName = result?.nextType?.className || null;
  const timeoutSeconds = typeof result?.timeout === 'number' ? result.timeout : null;
  const length = typeof result?.type?.length === 'number' ? result.type.length : null;
  const preferredType = options.preferredType || null;
  const degraded = Boolean(preferredType && typeName && typeName !== preferredType);

  return {
    type: typeName,
    nextType: nextTypeName,
    timeoutSeconds,
    length,
    preferredType,
    degraded,
    degradedReason: degraded ? 'telegram_selected_non_app_delivery' : null,
    authTransport: options.authTransport || null,
    authClientProfile: options.authClientProfile || null,
    attemptedDcIds: Array.isArray(options.attemptedDcIds) ? options.attemptedDcIds : [],
    connectedInitialDcId: options.connectedInitialDcId || null,
  };
}

function resetQrState(runtime) {
  runtime.qrAuthRunning = false;
  runtime.qrAuthStatus = 'idle';
  runtime.qrAuthError = null;
  runtime.qrAuthUser = null;
  runtime.latestQR = null;
  runtime.twoFaResolve = null;
  if (runtime.twoFaTimer) {
    clearTimeout(runtime.twoFaTimer);
    runtime.twoFaTimer = null;
  }
}

function qrExpiresAtMs(code) {
  const expires = code?.expires;
  if (expires instanceof Date) {
    return expires.getTime();
  }
  if (typeof expires === 'number' && Number.isFinite(expires)) {
    return expires > 10_000_000_000 ? expires : expires * 1000;
  }
  return Date.now() + 30_000;
}

function setQrAuthError(runtime, err) {
  const normalized = normalizeTelegramAuthError(err);
  runtime.qrAuthError = normalized;
  runtime.qrAuthStatus = normalized.code === 'AUTH_TOKEN_EXPIRED' ? 'expired' : 'failed';
  return normalized;
}

async function createClient(sessionString, persistTarget = undefined, clientOptions = {}) {
  const session = sessionStore.createSession(sessionString, persistTarget);
  await session.load();
  if (!sessionString && clientOptions.initialDcId) {
    const endpoint = TELEGRAM_DC_ENDPOINTS[clientOptions.initialDcId];
    if (endpoint) {
      session.setDC(clientOptions.initialDcId, endpoint.address, endpoint.port);
    }
  }
  const options = buildTelegramClientOptions({
    connectionRetries: clientOptions.connectionRetries ?? 5,
    autoReconnect: clientOptions.autoReconnect ?? true,
    timeoutSeconds: clientOptions.timeoutSeconds ?? 10,
    transport: clientOptions.transport ?? TELEGRAM_TRANSPORT,
    clientProfile: clientOptions.clientProfile ?? null,
  });
  return applyTelegramClientProfile(
    new TelegramClient(session, API_ID, API_HASH, options),
    { clientProfile: clientOptions.clientProfile ?? null },
  );
}

async function createMediaClient(sessionString, persistTarget = undefined) {
  const session = sessionStore.createSession(sessionString, persistTarget);
  await session.load();
  const options = buildTelegramClientOptions({
    connectionRetries: 3,
    autoReconnect: false,
    timeoutSeconds: 10,
    transport: TELEGRAM_TRANSPORT,
  });
  return new TelegramClient(session, API_ID, API_HASH, options);
}

async function resolveRuntimeSessionString(runtime, label = 'Background') {
  let sessionString = runtime.sessionString;
  if (!sessionString && runtime.workspaceId) {
    sessionString = await sessionStore.loadSessionString(runtime.workspaceId);
  }
  if (!sessionString && runtime.client) {
    try {
      sessionString = await sessionStore.snapshotSession(runtime.client);
      runtime.sessionString = sessionString;
    } catch (err) {
      console.warn(`[${label}] ${runtimeLabel(runtime)} failed to snapshot live session:`, err.message);
    }
  }
  return sessionString || '';
}

async function withRuntimeClient(runtime, fn) {
  if (!runtime?.client || runtime.connectionState !== 'connected') {
    throw new Error('No connected Telegram runtime client available');
  }
  return fn(runtime.client);
}

async function connectWithTimeout(client, maxAttempts = 3, timeoutMs = 10_000, retryDelayMs = 2000) {
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const connectPromise = client.connect();
      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error('CONNECT_TIMEOUT')), timeoutMs);
      });
      await Promise.race([connectPromise, timeoutPromise]);
      return;
    } catch (err) {
      const senderConnected = client.connected || client._sender?.isConnected?.();
      if (err.message === 'CONNECT_TIMEOUT' && senderConnected) {
        console.warn('[Sidecar] Connect timed out after transport became ready; continuing');
        return;
      }
      console.warn(`[Sidecar] Connect attempt ${attempt}/${maxAttempts} failed: ${err.message}`);
      if (attempt === maxAttempts) {
        throw new Error(`Failed to connect after ${maxAttempts} attempts`);
      }
      try {
        await client.disconnect();
      } catch {}
      if (retryDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
      }
    }
  }
}

async function ensureMe(runtime) {
  if (!runtime.client) return null;
  if (runtime.latestMe) return runtime.latestMe;
  try {
    const me = await withRpcTimeout(
      runtime.client.getMe(),
      `GET_ME_${runtime.workspaceId || runtime.tempSessionId || 'bootstrap'}`,
    );
    runtime.latestMe = me;
    return me;
  } catch (err) {
    await markRuntimeRpcFailure(runtime, err);
    return runtime.latestMe;
  }
}

function serializeCurrentPrivateDialogs(dialogs, meId = null) {
  return dialogs
    .map((dialog) => serializePrivateHumanDialog(dialog, meId))
    .filter(Boolean);
}

async function syncDialogShells(runtime, dialogs = null, rawDialogs = null) {
  const me = await ensureMe(runtime);
  if (!me?.id || !runtime.client) {
    return 0;
  }

  let serializedDialogs = dialogs;
  let sourceDialogs = rawDialogs;
  if (!serializedDialogs) {
    try {
      const liveDialogs = await runDurableTelegramMethod(
        runtime,
        {
          methodClass: 'dialog_sync',
          label: `GET_DIALOGS_${runtime.workspaceId}`,
          jobKind: 'dialog_sync',
          jobKey: 'dialog_shells',
          priority: 3,
        },
        () => withRuntimeClient(
          runtime,
          (telegramClient) => telegramClient.getDialogs({}),
        ),
      );
      sourceDialogs = liveDialogs;
      serializedDialogs = serializeCurrentPrivateDialogs(liveDialogs, me.id);
    } catch (err) {
      runtime.lastError = err.message;
      if (err.code === TELEGRAM_QUEUE_PAUSED) {
        console.warn(`[Dialogs] ${runtimeLabel(runtime)} dialog sync paused for ${err.retryAfter}s`);
      } else {
        console.warn(`[Dialogs] ${runtimeLabel(runtime)} dialog sync failed:`, err.message);
      }
      return 0;
    }
  }

  if (!serializedDialogs.length) {
    return 0;
  }

  telegramDurableState.rememberDialogState({
    runtime,
    dialogs: sourceDialogs || serializedDialogs,
    source: 'dialog_sync',
    syncedAt: nowSeconds(),
  }).catch((stateErr) => {
    runtime.lastDurableStateError = stateErr.message;
    console.warn(
      `[TelegramState] Durable dialog state write failed for ${runtimeLabel(runtime)}:`,
      stateErr.message,
    );
  });

  try {
    await postWebhookJson('/api/webhook/telegram/dialog-sync', {
      sellerUserId: String(me.id),
      dialogs: serializedDialogs,
    });
  } catch (err) {
    console.warn(`[Dialogs] Shell sync failed for ${runtimeLabel(runtime)}:`, err.message);
    return 0;
  }
  return serializedDialogs.length;
}

async function destroyRuntimeClient(runtime) {
  if (runtime.syncJobResumeTimer) {
    clearTimeout(runtime.syncJobResumeTimer);
    runtime.syncJobResumeTimer = null;
  }
  if (!runtime.client) return;
  try {
    await runtime.client.destroy();
  } catch (err) {
    console.warn(`[Sidecar] Failed to destroy ${runtimeLabel(runtime)} client:`, err.message);
  }
  runtime.client = null;
  runtime.handlersRegistered = false;
  runtime.handlersRegisteredAt = null;
}

function clearRuntimeAuthState(runtime) {
  runtime.connectionState = 'disconnected';
  runtime.reconnectAttempts = 0;
  runtime.latestMe = null;
  runtime.pendingPhoneCodeHash = null;
  runtime.handlersRegistered = false;
  runtime.handlersRegisteredAt = null;
  runtime.catchUpScheduledAt = null;
  runtime.catchUpStartedAt = null;
}

async function connectedRuntimeTelegramUser(runtime) {
  if (!isHealthyWorkspaceSession(runtime)) return null;
  try {
    return await ensureMe(runtime);
  } catch (err) {
    runtime.lastError = err.message;
    return null;
  }
}

async function destroyTempRuntime(tempSessionId) {
  const key = `temp:${tempSessionId}`;
  const runtime = runtimeRegistry.getByKey(key);
  if (!runtime) return;

  await destroyRuntimeClient(runtime);
  clearRuntimeAuthState(runtime);
  runtime.lastError = null;
  runtimeRegistry.deleteByKey(key);
}

function scheduleReconnect(runtime) {
  if (!runtime.workspaceId) return;
  if (runtime.connectionState === 'connected') return;
  if (runtime.connectPromise || runtime.reconnectTimer) return;

  runtime.connectionState = 'reconnecting';
  runtime.reconnectAttempts += 1;

  if (runtime.reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
    runtime.connectionState = 'failed';
    runtime.lastError = 'MAX_RECONNECT_ATTEMPTS_EXHAUSTED';
    console.error(
      `[GramJS] Max reconnect attempts exhausted for ${runtimeLabel(runtime)}`,
    );
    return;
  }

  const delay = Math.min(1000 * 2 ** (runtime.reconnectAttempts - 1), MAX_RECONNECT_DELAY);
  console.log(
    `[GramJS] Reconnecting ${runtimeLabel(runtime)} in ${delay}ms `
      + `(attempt ${runtime.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`,
  );

  runtime.reconnectTimer = setTimeout(async () => {
    runtime.reconnectTimer = null;
    try {
      await connectWorkspaceRuntime(runtime.workspaceId);
    } catch (err) {
      console.error(`[GramJS] Reconnect failed for ${runtimeLabel(runtime)}:`, err.message);
      scheduleReconnect(runtime);
    }
  }, delay);
}

async function forwardInboundMessage(runtime, msg, options = {}) {
  const telegramUpdateReceivedAt = options.telegramUpdateReceivedAt || nowSeconds();
  const connectorDecision = prepareInboundConnectorEvent({
    runtime,
    msg,
    source: options.source || (options.isHistorical ? 'history_sync' : 'live'),
    isHistorical: Boolean(options.isHistorical),
    nowSeconds: telegramUpdateReceivedAt,
    scheduleGapRepair,
  });
  if (connectorDecision.action !== 'forward') {
    return false;
  }
  let resolvedPeer = null;
  if (msg?.isPrivate && !cachedPrivatePeer(msg)) {
    // Unknown private peer: resolve the entity ONCE before forwarding so bots
    // cannot slip past the human filter while uncached. Live incident
    // (2026-06-10): a freshly created bot's DMs were ingested as a customer
    // conversation and the seller agent looped against it. Applies to ANY
    // bot, not just OQIM's own control bot. Resolve failure falls through to
    // the historical behavior (forward) so real new customers are never
    // dropped by a transient RPC error.
    try {
      const entity = await runtime.client.getEntity(msg.chatId || msg.peerId);
      if (!isPrivateHumanEntity(entity, runtime?.latestMe?.id ?? null)) {
        return false;
      }
      resolvedPeer = entity; // reuse for name/username in the event payload
    } catch (err) {
      console.warn(
        `[Inbound] Unknown-peer entity resolve failed for ${runtimeLabel(runtime)}:`,
        err.message,
      );
    }
  }
  const telegramStateAppliedAt = applyHotMessageState(runtime, msg, {
    telegramUpdateReceivedAt,
    resolvedPeer,
  });
  const event = buildHotInboundEvent({
    runtime,
    msg,
    resolvedPeer,
    isHistorical: Boolean(options.isHistorical),
    telegramUpdateReceivedAt,
    telegramStateAppliedAt,
    connectorTelemetry: connectorDecision.telemetry,
  });
  if (!event) {
    return;
  }

  try {
    const source = options.source || (options.isHistorical ? 'history_sync' : 'live');
    event.payload.outbox_enqueued_at = nowSeconds();
    await eventOutbox.enqueue(event);
    await eventOutbox.flush({ workspaceId: runtime.workspaceId, limit: 25 });
    const stateWrite = telegramDurableState.rememberHotMessageState({
      runtime,
      msg,
      source,
      receivedAt: telegramUpdateReceivedAt,
      appliedAt: telegramStateAppliedAt,
      resolvedPeer,
    });
    stateWrite.then(() => {
      if (msg?.media) {
        scheduleMediaHydration(runtime, 500);
      }
    }).catch((stateErr) => {
      runtime.lastDurableStateError = stateErr.message;
      console.warn(
        `[TelegramState] Durable state write failed for ${runtimeLabel(runtime)}:`,
        stateErr.message,
      );
    });
    const hotPathAt = new Date().toISOString();
    const hotPathLatencyMs = Math.max(
      0,
      Math.round((nowSeconds() - event.payload.telegram_update_received_at) * 1000),
    );
    runtime.lastInboundHotPathAt = hotPathAt;
    runtime.lastInboundHotPathLatencyMs = hotPathLatencyMs;
    runtime.lastInboundHotPathSource = source;
    if (!options.isHistorical && !event.payload.isOutgoing) {
      runtime.lastLiveInboundHotPathAt = hotPathAt;
      runtime.lastLiveInboundHotPathLatencyMs = hotPathLatencyMs;
    }
    return true;
  } catch (err) {
    runtime.lastError = err.message;
    console.error(
      `[Inbound] Durable append failed for ${runtimeLabel(runtime)}:`,
      err.message,
    );
    return false;
  }
}

async function forwardEditedMessage(runtime, msg) {
  const me = await ensureMe(runtime);
  if (!me?.id) {
    throw new Error('Seller identity unavailable for edit event');
  }
  if (!msg.chatId) {
    throw new Error('chatId required for append-only edit event');
  }

  const editedAt = safeNumber(msg.editDate) || Math.floor(Date.now() / 1000);
  const event = buildEditedEvent({
    workspaceId: runtime.workspaceId,
    sellerUserId: me.id,
    msg,
    editedAt,
  });
  await eventOutbox.enqueue(event);
  await eventOutbox.flush({ workspaceId: runtime.workspaceId, limit: 25 });
}

async function forwardDeletedMessage(runtime, event) {
  const me = await ensureMe(runtime);
  if (!me?.id) {
    throw new Error('Seller identity unavailable for delete event');
  }

  const chatId = event.chatId || event.peer?.channelId || null;
  if (!chatId) {
    throw new Error('chatId required for append-only delete event');
  }
  const deletedAt = Math.floor(Date.now() / 1000);
  const messageIds = event.deletedIds || [];
  const deletionEvent = buildDeletedEvent({
    workspaceId: runtime.workspaceId,
    sellerUserId: me.id,
    chatId,
    messageIds,
    deletedAt,
  });
  await eventOutbox.enqueue(deletionEvent);
  await eventOutbox.flush({ workspaceId: runtime.workspaceId, limit: 25 });
}

async function syncUnreadDialogs(runtime) {
  if (!runtime.client || runtime.connectionState !== 'connected') {
    return 0;
  }
  if (runtime.catchUpInFlight) {
    return 0;
  }

  let synced = 0;
  runtime.catchUpInFlight = true;
  runtime.catchUpStartedAt = new Date().toISOString();
  try {
    const me = await ensureMe(runtime);
    await withRuntimeClient(runtime, async (telegramClient) => {
      const dialogs = await runDurableTelegramMethod(
        runtime,
        {
          methodClass: 'dialog_sync',
          label: `GET_DIALOGS_${runtime.workspaceId}`,
          jobKind: 'unread_catchup',
          jobKey: 'dialogs',
          priority: 3,
        },
        () => withRpcTimeout(
          telegramClient.getDialogs({}),
          `GET_DIALOGS_${runtime.workspaceId}`,
          CATCH_UP_TIMEOUT_MS,
        ),
      );
      const privateDialogs = serializeCurrentPrivateDialogs(dialogs, me?.id ?? null);
      if (privateDialogs.length) {
        await syncDialogShells(runtime, privateDialogs, dialogs);
      }
      for (const dialog of dialogs) {
        if (!isPrivateHumanDialog(dialog, me?.id ?? null)) continue;
        const unreadCount = Number(dialog.unreadCount || 0);
        if (unreadCount <= 0) continue;

        const limit = Math.min(unreadCount, 50);
        const messages = await runDurableTelegramMethod(
          runtime,
          {
            methodClass: 'history_sync',
            label: `GET_UNREAD_MESSAGES_${runtime.workspaceId}`,
            jobKind: 'unread_catchup',
            jobKey: `unread:${dialog.id || dialog.entity?.id || dialog.inputEntity?.id || 'unknown'}`,
            priority: 4,
            cursor: { limit },
          },
          () => withRpcTimeout(
            telegramClient.getMessages(dialog.inputEntity, { limit }),
            `GET_UNREAD_MESSAGES_${runtime.workspaceId}`,
            CATCH_UP_TIMEOUT_MS,
          ),
        );
        const orderedMessages = [...messages]
          .filter((message) => message && message.id)
          .sort((a, b) => (a.date || 0) - (b.date || 0));

        for (const message of orderedMessages) {
          const promoteToLiveRecovery = shouldPromoteCatchUpMessageToLiveRecovery(
            runtime,
            message,
            { nowSeconds: nowSeconds() },
          );
          if (await forwardInboundMessage(runtime, message, {
            isHistorical: !promoteToLiveRecovery,
            source: promoteToLiveRecovery ? 'live_recovery' : 'history_sync',
          })) {
            synced += 1;
          }
        }

        await sleep(500);
      }
    });
    runtime.catchUpFailureCount = 0;
    runtime.lastCatchUpSuccessAt = new Date().toISOString();
  } catch (err) {
    if (err.code !== TELEGRAM_QUEUE_PAUSED) {
      runtime.catchUpFailureCount = (runtime.catchUpFailureCount || 0) + 1;
    }
    runtime.lastError = err.message;
    console.warn(
      `[CatchUp] Failed for ${runtimeLabel(runtime)} (consecutive=${runtime.catchUpFailureCount}):`,
      err.message,
    );
  } finally {
    runtime.catchUpInFlight = false;
    runtime.catchUpStartedAt = null;
  }

  runtime.lastCatchUpAt = new Date().toISOString();
  runtime.lastCatchUpCount = synced;
  if (synced > 0) {
    console.log(`[CatchUp] Synced ${synced} unread historical messages for ${runtimeLabel(runtime)}`);
  }
  return synced;
}

function scheduleCatchUp(runtime, delayMs = 0) {
  if (!SIDECAR_BACKGROUND_SYNC_ENABLED) return;
  if (!runtime.workspaceId || runtime.catchUpInFlight || runtime.catchUpTimer) return;
  runtime.catchUpScheduledAt = new Date().toISOString();
  runtime.catchUpTimer = setTimeout(() => {
    runtime.catchUpTimer = null;
    syncUnreadDialogs(runtime).catch((err) => {
      runtime.lastError = err.message;
      console.warn(`[CatchUp] Deferred sync failed for ${runtimeLabel(runtime)}:`, err.message);
    });
  }, delayMs);
}

function scheduleGapRepair(runtime, delayMs = 0) {
  if (!SIDECAR_BACKGROUND_SYNC_ENABLED) return;
  if (!runtime.workspaceId || runtime.gapRepairInFlight || runtime.gapRepairTimer) return;
  runtime.gapRepairScheduledAt = new Date().toISOString();
  runtime.gapRepairTimer = setTimeout(() => {
    runtime.gapRepairTimer = null;
    if (runtime.connectionState !== 'connected') {
      return;
    }
    if (runtime.catchUpInFlight || runtime.catchUpTimer) {
      scheduleGapRepair(runtime, 1000);
      return;
    }
    runtime.gapRepairInFlight = true;
    repairTelegramUpdateGap({
      runtime,
      durableStateStore: telegramDurableState,
      runQueuedTelegramMethod: runDurableTelegramMethod,
      withRpcTimeout,
      withBackgroundClient: withRuntimeClient,
      forwardInboundMessage,
      runtimeLabel,
    }).catch((err) => {
      runtime.lastSyncJobError = err.message;
      console.warn(`[GapRepair] Failed for ${runtimeLabel(runtime)}:`, err.message);
    }).finally(() => {
      runtime.gapRepairInFlight = false;
    });
  }, delayMs);
}

function scheduleMediaHydration(runtime, delayMs = 0) {
  if (!SIDECAR_BACKGROUND_SYNC_ENABLED) return;
  if (!SIDECAR_MEDIA_HYDRATION_ENABLED) return;
  if (!runtime.workspaceId || runtime.mediaHydrationInFlight || runtime.mediaHydrationTimer) {
    return;
  }

  runtime.mediaHydrationTimer = setTimeout(() => {
    runtime.mediaHydrationTimer = null;
    if (runtime.connectionState !== 'connected') {
      return;
    }
    if (
      runtime.catchUpInFlight
      || runtime.catchUpTimer
      || runtime.gapRepairInFlight
      || runtime.gapRepairTimer
    ) {
      scheduleMediaHydration(runtime, 2000);
      return;
    }
    runtime.mediaHydrationInFlight = true;
    hydratePendingMediaRefs({
      runtime,
      durableStateStore: telegramDurableState,
      runQueuedTelegramMethod: runDurableTelegramMethod,
      withIsolatedMediaClient,
      withRpcTimeout,
      resolvePeer: telegramPeerResolver.resolve,
      onHydratedMediaRef: (ref, payload) => postHydratedMediaRefToBackend(
        { postJson: postWebhookJson },
        ref,
        payload,
      ),
    }).then((result) => {
      runtime.lastMediaHydrationAt = new Date().toISOString();
      runtime.lastMediaHydrationResult = result;
      if ((result.hydrated + result.failed) >= 10 && result.paused === 0) {
        scheduleMediaHydration(runtime, 1000);
      }
    }).catch((err) => {
      runtime.lastMediaHydrationError = err.message;
      console.warn(`[MediaHydration] Failed for ${runtimeLabel(runtime)}:`, err.message);
    }).finally(() => {
      runtime.mediaHydrationInFlight = false;
    });
  }, delayMs);
}

function scheduleSyncJobResume(runtime, delayMs = 1500) {
  if (!SIDECAR_BACKGROUND_SYNC_ENABLED) return;
  if (!runtime.workspaceId || runtime.syncJobResumeInFlight || runtime.syncJobResumeTimer) {
    return;
  }

  runtime.syncJobResumeTimer = setTimeout(async () => {
    runtime.syncJobResumeTimer = null;
    if (runtime.connectionState !== 'connected') {
      return;
    }
    runtime.syncJobResumeInFlight = true;
    try {
      await resumeTelegramSyncJobs({
        runtime,
        syncJobStore: telegramSyncJobs,
        syncDialogShells,
        scheduleCatchUp,
        scheduleGapRepair,
        scheduleMediaHydration,
        runtimeLabel,
      });
    } finally {
      runtime.syncJobResumeInFlight = false;
    }
  }, delayMs);
}

// Throttle typing signals: at most one POST per chat per window. Telegram
// re-emits UpdateUserTyping every ~5s while the user keeps typing, and the
// backend hold window is 4s — one signal per ~3s keeps the hold alive.
const TYPING_SIGNAL_THROTTLE_MS = 3000;
const typingSignalLastSentAt = new Map();

function forwardTypingSignal(runtime, update) {
  const userId = update?.userId?.value ?? update?.userId;
  if (!userId || !runtime?.workspaceId) return;
  const me = runtime?.latestMe;
  if (me?.id && String(userId) === String(me.id)) return; // own typing
  const key = `${runtime.workspaceId}:${userId}`;
  const now = Date.now();
  const last = typingSignalLastSentAt.get(key) || 0;
  if (now - last < TYPING_SIGNAL_THROTTLE_MS) return;
  typingSignalLastSentAt.set(key, now);
  // fire-and-forget: a lost typing signal just means slightly earlier dispatch
  postWebhookJson('/api/webhook/telegram/typing', {
    sellerUserId: String(me?.id || ''),
    workspaceId: runtime.workspaceId,
    chatId: String(userId),
  }).catch(() => {});
}

function registerEventHandlers(runtime) {
  startTelegramUpdatePump(runtime, {
    forwardInboundMessage,
    forwardEditedMessage,
    forwardDeletedMessage,
    forwardTypingSignal,
    scheduleReconnect,
    runtimeLabel,
    nowSeconds,
  });
}

async function connectWorkspaceRuntime(workspaceId) {
  const runtime = getWorkspaceRuntime(workspaceId);
  if (runtime.connectPromise) {
    return runtime.connectPromise;
  }

  runtime.connectPromise = (async () => {
    const enabled = await isStoredSessionEnabled(pool, workspaceId);
    if (!enabled) {
      runtime.lastError = 'SESSION_DISABLED';
      runtime.connectionState = 'disconnected';
      return false;
    }
    if (runtime.reconnectTimer) {
      clearTimeout(runtime.reconnectTimer);
      runtime.reconnectTimer = null;
    }
    const sessionRecord = await sessionStore.loadSessionRecord(workspaceId);
    return restoreWorkspaceSession({
      workspaceId,
      runtime,
      sessionRecord,
      transportCandidates: buildWorkspaceRestoreTransports(sessionRecord.transport, TELEGRAM_TRANSPORT),
      createClient,
      connectWithTimeout,
      withRpcTimeout,
      destroyRuntimeClient,
      sessionStore,
      registerEventHandlers,
      scheduleCatchUp,
      scheduleGapRepair,
      scheduleMediaHydration,
      scheduleSyncJobResume,
      scheduleReconnect,
      normalizeTelegramAuthError,
      runtimeLabel,
    });
  })();

  try {
    const connected = await runtime.connectPromise;
    if (connected) {
      telegramDurableState.pruneLegacyPrivateHotPathCursors?.(workspaceId)
        .then((pruned) => {
          if (pruned > 0) {
            console.log(`[TelegramState] Pruned ${pruned} legacy private hot-path cursor(s) for workspace ${workspaceId}`);
          }
        })
        .catch((err) => {
          runtime.lastDurableStateError = err.message;
          console.warn(`[TelegramState] Legacy cursor prune failed for ${runtimeLabel(runtime)}:`, err.message);
        });
    }
    return connected;
  } finally {
    runtime.connectPromise = null;
  }
}

async function persistConnectedWorkspaceRuntime(runtime) {
  if (!runtime?.workspaceId || runtime.connectionState !== 'connected' || !runtime.client) {
    return false;
  }
  const me = await ensureMe(runtime);
  if (!me) {
    throw new Error('Connected runtime is missing Telegram user info');
  }
  const sessionString = await sessionStore.snapshotSession(runtime.client);
  runtime.sessionString = sessionString;
  await sessionStore.saveSessionString(runtime.workspaceId, sessionString, {
    transport: runtime.transport,
    clientProfile: runtime.clientProfile,
  });
  const persisted = await pool.query(
    'SELECT 1 FROM telegram_sessions WHERE workspace_id = $1',
    [runtime.workspaceId],
  );
  if (persisted.rowCount < 1) {
    throw new Error('Connected runtime session was not persisted');
  }
  sessionStore.retargetRuntimeSession(
    runtime,
    sessionStore.persistenceTargetForRuntime(runtime),
  );
  return true;
}

async function ensureTransport(runtime, sessionString = '') {
  if (runtime.client) {
    return runtime.client;
  }

  const transportCandidates = [
    runtime.authTransport,
    runtime.transport,
    TELEGRAM_TRANSPORT,
    'tcp',
  ].filter((transport, index, transports) => (
    transport && transports.indexOf(transport) === index
  ));
  const failures = [];

  for (const transport of transportCandidates) {
    runtime.client = await createClient(
      sessionString,
      sessionStore.persistenceTargetForRuntime(runtime),
      { autoReconnect: !runtime.tempSessionId, transport },
    );
    try {
      await connectWithTimeout(runtime.client);
      runtime.transport = transport;
      return runtime.client;
    } catch (err) {
      failures.push(`${transport}:${err.message}`);
      await destroyRuntimeClient(runtime);
    }
  }

  throw new Error(`TRANSPORT_CONNECT_FAILED ${failures.join(', ')}`);
}

function resolvePhoneAuthClientProfile(body = {}) {
  const raw = typeof body.authClientProfile === 'string'
    ? body.authClientProfile.trim().toLowerCase()
    : TELEGRAM_AUTH_CLIENT_PROFILE;
  if (!raw || raw === 'default' || raw === 'none') {
    return null;
  }
  return raw;
}

async function ensurePhoneAuthTransport(runtime, options = {}) {
  if (runtime.client) {
    return runtime.client;
  }

  const transport = resolveTelegramAuthTransport(process.env, options.transport);
  const clientProfile = options.clientProfile === undefined
    ? TELEGRAM_AUTH_CLIENT_PROFILE
    : options.clientProfile;
  const failures = [];
  runtime.authTransport = transport;
  runtime.authClientProfile = clientProfile;
  runtime.authAttemptedDcIds = [];
  runtime.authConnectedInitialDcId = null;

  for (const dcId of AUTH_INITIAL_DCS) {
    runtime.authAttemptedDcIds.push(dcId);
    runtime.client = await createClient('', sessionStore.persistenceTargetForRuntime(runtime), {
      autoReconnect: false,
      connectionRetries: 1,
      initialDcId: dcId,
      timeoutSeconds: Math.max(3, Math.ceil(AUTH_CONNECT_TIMEOUT_MS / 1000)),
      transport,
      clientProfile,
    });
    try {
      console.log(`[Phone Auth] Connecting temp auth client via DC${dcId} (${transport}/${clientProfile || 'default'})`);
      await connectWithTimeout(runtime.client, 1, AUTH_CONNECT_TIMEOUT_MS, 0);
      runtime.authConnectedInitialDcId = dcId;
      return runtime.client;
    } catch (err) {
      failures.push(`DC${dcId}:${err.message}`);
      console.warn(`[Phone Auth] Temp auth connect failed on DC${dcId} (${transport}/${clientProfile || 'default'}): ${err.message}`);
      await destroyRuntimeClient(runtime);
    }
  }

  throw new Error(`PHONE_AUTH_CONNECT_FAILED ${failures.join(', ')}`);
}

async function restoreTempRuntimeFromBody(runtime, body) {
  if (body.tempSessionString && !runtime.client) {
    runtime.sessionString = String(body.tempSessionString);
  }
  if (body.authTransport && !runtime.authTransport) {
    runtime.authTransport = normalizeTelegramTransport(body.authTransport);
  }
  if (body.authClientProfile && !runtime.authClientProfile) {
    runtime.authClientProfile = String(body.authClientProfile).trim().toLowerCase();
  }
  if (body.phoneCodeHash && !runtime.pendingPhoneCodeHash) {
    runtime.pendingPhoneCodeHash = String(body.phoneCodeHash);
  }
}

async function snapshotTempAuthSession(runtime) {
  if (!runtime.client) return null;
  try {
    const serialized = await sessionStore.snapshotSession(runtime.client);
    runtime.sessionString = serialized;
    return serialized;
  } catch (err) {
    console.warn(`[Phone Auth] Failed to snapshot ${runtimeLabel(runtime)} temp session:`, err.message);
    return runtime.sessionString || null;
  }
}

async function ensureAuthorizedRuntime(workspaceId) {
  const runtime = getWorkspaceRuntime(workspaceId);
  if (runtime.client && runtime.connectionState === 'connected') {
    return runtime;
  }
  await connectWorkspaceRuntime(workspaceId);
  return runtime;
}

async function withIsolatedMediaClient(runtime, fn) {
  const sessionString = await resolveRuntimeSessionString(runtime, 'Media');
  if (!sessionString) {
    throw new Error('No session available for media download');
  }

  const mediaClient = await createMediaClient(
    sessionString,
    undefined,
  );
  try {
    await connectWithTimeout(mediaClient);
    const result = await fn(mediaClient);
    return result;
  } finally {
    try {
      await mediaClient.disconnect();
    } catch {}
    try {
      await mediaClient.destroy();
    } catch {}
  }
}

async function requestSentCode(client, phoneNumber) {
  try {
    const result = await client.invoke(
      new Api.auth.SendCode({
        phoneNumber,
        apiId: API_ID,
        apiHash: API_HASH,
        settings: new Api.CodeSettings({}),
      }),
    );

    if (result instanceof Api.auth.SentCodeSuccess) {
      throw new Error('Already authorized');
    }

    return result;
  } catch (err) {
    if (err?.errorMessage === 'AUTH_RESTART') {
      return requestSentCode(client, phoneNumber);
    }
    throw err;
  }
}

async function cancelSentCode(client, phoneNumber, phoneCodeHash) {
  if (!phoneCodeHash) return false;
  try {
    return await client.invoke(
      new Api.auth.CancelCode({
        phoneNumber,
        phoneCodeHash,
      }),
    );
  } catch (err) {
    console.warn(`[Phone Auth] cancelCode failed for ${phoneNumber}:`, err.message);
    return false;
  }
}

async function bindBootstrapToWorkspace(workspaceId, expectedUserId = null) {
  const bootstrap = getBootstrapRuntime();
  if (!bootstrap.client) {
    const sessionString = await sessionStore.loadSessionString(null);
    if (!sessionString) {
      throw new Error('No bootstrap session to bind');
    }
    bootstrap.client = await createClient(sessionString, null);
    await connectWithTimeout(bootstrap.client);
  }

  const me = await ensureMe(bootstrap);
  if (!me) {
    throw new Error('Bootstrap session is not authorized');
  }
  if (expectedUserId && String(me.id) !== String(expectedUserId)) {
    throw new Error('Bootstrap session belongs to a different Telegram user');
  }

  const runtime = getWorkspaceRuntime(workspaceId);
  const existingMe = await connectedRuntimeTelegramUser(runtime);
  if (existingMe && telegramUserIdsMatch(existingMe.id, me.id)) {
    const shouldDiscardDuplicate = runtime.client !== bootstrap.client;
    if (shouldDiscardDuplicate) {
      await discardRuntimeAuthorization({
        runtime: bootstrap,
        reason: 'duplicate bootstrap login for already connected workspace',
        destroyRuntimeClient,
        runtimeLabel,
      });
    }
    await sessionStore.saveSessionString(null, '');
    bootstrap.client = null;
    clearRuntimeAuthState(bootstrap);
    resetQrState(bootstrap);
    return {
      workspaceId,
      reused: true,
      replaced: false,
      discardedDuplicate: shouldDiscardDuplicate,
      revokedDuplicate: false,
      user: {
        userId: String(existingMe.id),
        phone: existingMe.phone ? `+${existingMe.phone}` : '',
        firstName: existingMe.firstName || '',
        lastName: existingMe.lastName || '',
      },
    };
  }
  if (existingMe && !telegramUserIdsMatch(existingMe.id, me.id)) {
    await discardRuntimeAuthorization({
      runtime: bootstrap,
      reason: 'bootstrap login does not match connected workspace user',
      destroyRuntimeClient,
      runtimeLabel,
    });
    await sessionStore.saveSessionString(null, '');
    clearRuntimeAuthState(bootstrap);
    resetQrState(bootstrap);
    throw new Error('Workspace is already connected to a different Telegram user');
  }
  if (runtime.client && runtime.client !== bootstrap.client) {
    await destroyRuntimeClient(runtime);
  }

  runtime.client = bootstrap.client;
  runtime.connectionState = 'connected';
  runtime.reconnectAttempts = 0;
  runtime.lastError = null;
  runtime.transport = TELEGRAM_TRANSPORT;
  runtime.latestMe = me;
  runtime.pendingPhoneCodeHash = null;
  runtime.handlersRegistered = false;
  runtime.handlersRegisteredAt = null;
  sessionStore.retargetRuntimeSession(runtime, workspaceId);

  const sessionString = await sessionStore.snapshotSession(runtime.client);
  runtime.sessionString = sessionString;
  await sessionStore.saveSessionString(workspaceId, sessionString, { transport: runtime.transport });
  registerEventHandlers(runtime);
  scheduleCatchUp(runtime, 1000);
  scheduleGapRepair(runtime, 1800);
  scheduleMediaHydration(runtime, 2200);

  bootstrap.client = null;
  clearRuntimeAuthState(bootstrap);
  resetQrState(bootstrap);
  await sessionStore.saveSessionString(null, '');

  return {
    workspaceId,
    reused: false,
    replaced: true,
    user: {
      userId: String(me.id),
      phone: me.phone ? `+${me.phone}` : '',
      firstName: me.firstName || '',
      lastName: me.lastName || '',
    },
  };
}

async function registerTempSessionToWorkspace(tempSessionId, workspaceId, expectedUserId = null) {
  const tempRuntime = getTempRuntime(tempSessionId);
  if (!tempRuntime.client || tempRuntime.connectionState !== 'connected') {
    throw new Error('Temporary session is not authorized');
  }

  const me = await ensureMe(tempRuntime);
  if (!me) {
    throw new Error('Temporary session is missing Telegram user info');
  }
  if (expectedUserId && !telegramUserIdsMatch(me.id, expectedUserId)) {
    throw new Error('Temporary session belongs to a different Telegram user');
  }

  const runtime = getWorkspaceRuntime(workspaceId);
  const existingMe = await connectedRuntimeTelegramUser(runtime);
  if (existingMe && telegramUserIdsMatch(existingMe.id, me.id)) {
    const shouldDiscardDuplicate = runtime.client !== tempRuntime.client;
    if (shouldDiscardDuplicate) {
      await discardRuntimeAuthorization({
        runtime: tempRuntime,
        reason: 'duplicate temp login for already connected workspace',
        destroyRuntimeClient,
        runtimeLabel,
      });
    }
    tempRuntime.client = null;
    clearRuntimeAuthState(tempRuntime);
    runtimeRegistry.deleteByKey(tempRuntime.key);
    return {
      workspaceId,
      reused: true,
      replaced: false,
      discardedDuplicate: shouldDiscardDuplicate,
      revokedDuplicate: false,
      user: {
        userId: String(existingMe.id),
        phone: existingMe.phone ? `+${existingMe.phone}` : '',
        firstName: existingMe.firstName || '',
        lastName: existingMe.lastName || '',
      },
    };
  }
  if (existingMe && !telegramUserIdsMatch(existingMe.id, me.id)) {
    await discardRuntimeAuthorization({
      runtime: tempRuntime,
      reason: 'temp login does not match connected workspace user',
      destroyRuntimeClient,
      runtimeLabel,
    });
    clearRuntimeAuthState(tempRuntime);
    runtimeRegistry.deleteByKey(tempRuntime.key);
    throw new Error('Workspace is already connected to a different Telegram user');
  }
  if (runtime.client && runtime.client !== tempRuntime.client) {
    await destroyRuntimeClient(runtime);
  }

  runtime.client = tempRuntime.client;
  runtime.connectionState = 'connected';
  runtime.reconnectAttempts = 0;
  runtime.lastError = null;
  runtime.transport = tempRuntime.authTransport || tempRuntime.transport || TELEGRAM_TRANSPORT;
  runtime.clientProfile = tempRuntime.authClientProfile || tempRuntime.clientProfile || null;
  runtime.latestMe = me;
  runtime.pendingPhoneCodeHash = null;
  runtime.handlersRegistered = false;
  runtime.handlersRegisteredAt = null;
  sessionStore.retargetRuntimeSession(runtime, workspaceId);
  runtime.sessionString = await sessionStore.snapshotSession(runtime.client);

  await sessionStore.saveSessionString(workspaceId, runtime.sessionString, {
    transport: runtime.transport,
    clientProfile: runtime.clientProfile,
  });
  registerEventHandlers(runtime);
  scheduleCatchUp(runtime, 1000);
  scheduleGapRepair(runtime, 1800);
  scheduleMediaHydration(runtime, 2200);

  tempRuntime.client = null;
  clearRuntimeAuthState(tempRuntime);
  runtimeRegistry.deleteByKey(tempRuntime.key);

  return {
    workspaceId,
    reused: false,
    replaced: true,
    user: {
      userId: String(me.id),
      phone: me.phone ? `+${me.phone}` : '',
      firstName: me.firstName || '',
      lastName: me.lastName || '',
    },
  };
}

async function restoreStoredSessions() {
  // Restore EVERY workspace that has a persisted session — not just recently
  // active ones. The lazy-boot recency window silently left sessions offline
  // after the server was paused longer than the window (e.g. SATStation idle
  // since the prior day), forcing a manual reconnect. A dead/revoked session
  // just fails its connect and enters the normal reconnect/backoff path.
  const workspaceIds = await listStoredSessionWorkspaceIds(pool);
  const recentlyActive = await listRecentlyActiveWorkspaceIds(pool, LAZY_BOOT_ACTIVE_HOURS);
  console.log(
    `[Session] Boot: restoring ${workspaceIds.length} stored session(s) `
      + `(${recentlyActive.length} active in last ${LAZY_BOOT_ACTIVE_HOURS}h)`,
  );
  for (const workspaceId of workspaceIds) {
    try {
      await connectWorkspaceRuntime(workspaceId);
    } catch (err) {
      console.warn(`[Session] Boot restore failed for workspace ${workspaceId}:`, err.message);
    }
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/health') {
    const active = runtimeRegistry.list().filter((runtime) => runtime.connectionState === 'connected');
    const liveUpdates = summarizeLiveUpdateHealth(active);
    const baseHealth = {
      ok: true,
      active: active.length,
      total: runtimeRegistry.size(),
      liveUpdates,
    };
    if (!isAuthenticatedRequest(req)) {
      return json(res, 200, baseHealth);
    }
    return json(res, 200, {
      ...baseHealth,
      sessions: await Promise.all(
        runtimeRegistry.list()
          .map((runtime) => serializeRuntimeStatus(
            runtime,
            eventOutbox,
            telegramSyncJobs,
            telegramDurableState,
          )),
      ),
    });
  }

  if (!checkAuth(req, res)) return;

  try {
    if (req.method === 'GET' && url.pathname === '/status') {
      const workspaceId = parseWorkspaceId(url.searchParams.get('workspaceId'));
      const runtime = workspaceId ? getWorkspaceRuntime(workspaceId) : getBootstrapRuntime();
      if (
        workspaceId
        && runtime.connectionState !== 'connected'
        && !runtime.connectPromise
        && await isStoredSessionEnabled(pool, workspaceId)
        && runtime.lastError !== 'SESSION_REVOKED'
      ) {
        withRpcTimeout(connectWorkspaceRuntime(workspaceId), `STATUS_CONNECT_${workspaceId}`, 3_000)
          .catch((err) => {
            runtime.lastError = err.message;
          });
      }

      return json(
        res,
        200,
        await serializeRuntimeStatus(runtime, eventOutbox, telegramSyncJobs, telegramDurableState),
      );
    }

    if (req.method === 'GET' && url.pathname === '/sessions') {
      const sessions = await Promise.all(
        runtimeRegistry.list()
          .filter((runtime) => runtime.workspaceId)
          .map((runtime) => serializeRuntimeStatus(
            runtime,
            eventOutbox,
            telegramSyncJobs,
            telegramDurableState,
          )),
      );
      return json(res, 200, sessions);
    }

    if (req.method === 'POST' && url.pathname === '/runtime/cleanup-stale-workspaces') {
      const body = await parseBody(req);
      const activeWorkspaceIds = Array.isArray(body.activeWorkspaceIds)
        ? body.activeWorkspaceIds.map((workspaceId) => Number(workspaceId)).filter((workspaceId) => workspaceId > 0)
        : [];
      const staleRuntimes = runtimeRegistry.staleWorkspaceRuntimes(activeWorkspaceIds);
      const removed = [];

      for (const runtime of staleRuntimes) {
        await destroyRuntimeClient(runtime);
        clearRuntimeAuthState(runtime);
        resetQrState(runtime);
        runtimeRegistry.deleteByKey(runtime.key);
        removed.push({ workspaceId: runtime.workspaceId, key: runtime.key });
      }

      return json(res, 200, {
        activeWorkspaceIds,
        removed,
        removedCount: removed.length,
      });
    }

    const sessionStatusMatch = req.method === 'GET'
      ? url.pathname.match(/^\/sessions\/(\d+)\/status$/)
      : null;
    if (sessionStatusMatch) {
      const workspaceId = parseWorkspaceId(sessionStatusMatch[1]);
      const runtime = workspaceId ? getWorkspaceRuntime(workspaceId) : null;
      if (!runtime) {
        return json(res, 404, { error: 'Session not found' });
      }

      return json(
        res,
        200,
        await serializeRuntimeStatus(runtime, eventOutbox, telegramSyncJobs, telegramDurableState),
      );
    }

    if (req.method === 'POST' && url.pathname === '/send') {
      return handleSendRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/edit') {
      return handleEditRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/react') {
      return handleReactRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/typing') {
      return handleTypingRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/read') {
      return handleReadRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/online') {
      return handleOnlineRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/qr-auth/start') {
      const runtime = getBootstrapRuntime();
      if (runtime.connectionState === 'connected') {
        return json(res, 200, { status: 'already_connected' });
      }
      if (runtime.qrAuthRunning) {
        return json(res, 200, { status: 'already_started' });
      }

      runtime.qrAuthRunning = true;
      runtime.qrAuthStatus = 'waiting';
      runtime.qrAuthError = null;
      runtime.qrAuthUser = null;
      runtime.latestQR = null;
      runtime.twoFaResolve = null;
      if (runtime.twoFaTimer) {
        clearTimeout(runtime.twoFaTimer);
        runtime.twoFaTimer = null;
      }

      (async () => {
        try {
          await destroyRuntimeClient(runtime);
          runtime.client = await createClient('', null, { autoReconnect: false });
          await connectWithTimeout(runtime.client);

          await runtime.client.signInUserWithQrCode(
            { apiId: API_ID, apiHash: API_HASH },
            {
              qrCode: async (code) => {
                const tokenB64 = code.token.toString('base64url');
                const tgUrl = `tg://login?token=${tokenB64}`;
                runtime.latestQR = {
                  tgUrl,
                  svg: await QRCode.toString(tgUrl, {
                    type: 'svg',
                    margin: 2,
                    color: { dark: '#000000', light: '#ffffff' },
                  }),
                  expiresAt: qrExpiresAtMs(code),
                };
              },
              password: async () => {
                runtime.qrAuthStatus = '2fa_required';
                if (runtime.qrAuthError?.code !== 'PASSWORD_HASH_INVALID') {
                  runtime.qrAuthError = null;
                }
                return new Promise((resolve, reject) => {
                  runtime.twoFaResolve = resolve;
                  runtime.twoFaTimer = setTimeout(() => {
                    if (runtime.twoFaResolve) {
                      runtime.twoFaResolve = null;
                      runtime.twoFaTimer = null;
                      reject(new Error('2FA_TIMEOUT'));
                    }
                  }, 300_000);
                });
              },
              onError: (err) => {
                const normalized = normalizeTelegramAuthError(err);
                runtime.qrAuthError = normalized;
                console.error('[QR Auth] Error:', normalized.code);
                if (normalized.code === 'PASSWORD_HASH_INVALID') {
                  runtime.qrAuthStatus = '2fa_required';
                  return true;
                }
                if (normalized.code === '2FA_TIMEOUT') {
                  setQrAuthError(runtime, err);
                  return false;
                }
                return true;
              },
            },
          );

          runtime.connectionState = 'connected';
          runtime.reconnectAttempts = 0;
          const me = await ensureMe(runtime);
          await sessionStore.saveSessionString(
            null,
            await sessionStore.snapshotSession(runtime.client),
          );
          runtime.qrAuthUser = {
            userId: me?.id ? String(me.id) : '',
            phone: me?.phone ? `+${me.phone}` : '',
            firstName: me?.firstName || '',
            lastName: me?.lastName || '',
          };
          runtime.qrAuthStatus = 'success';
          runtime.qrAuthError = null;
        } catch (err) {
          const normalized = setQrAuthError(runtime, err);
          console.error('[QR Auth] Failed:', normalized.code);
          runtime.connectionState = 'disconnected';
        } finally {
          runtime.qrAuthRunning = false;
          if (runtime.twoFaTimer) {
            clearTimeout(runtime.twoFaTimer);
            runtime.twoFaTimer = null;
          }
          runtime.twoFaResolve = null;
        }
      })();

      return json(res, 200, { status: 'started' });
    }

    if (req.method === 'POST' && url.pathname === '/qr-auth/password') {
      const runtime = getBootstrapRuntime();
      const body = await parseBody(req);
      if (!body.password) {
        return json(res, 400, { error: 'password is required' });
      }
      if (!runtime.twoFaResolve) {
        return json(res, 409, { error: 'No pending 2FA request. Scan QR code first.' });
      }
      const resolve = runtime.twoFaResolve;
      runtime.twoFaResolve = null;
      if (runtime.twoFaTimer) {
        clearTimeout(runtime.twoFaTimer);
        runtime.twoFaTimer = null;
      }
      runtime.qrAuthStatus = 'password_submitted';
      runtime.qrAuthError = null;
      resolve(body.password);
      return json(res, 200, { status: 'password_submitted' });
    }

    if (req.method === 'GET' && url.pathname === '/qr-auth/status') {
      const runtime = getBootstrapRuntime();
      return json(res, 200, {
        status: runtime.qrAuthStatus || 'idle',
        user: runtime.qrAuthUser || null,
        error: runtime.qrAuthError || null,
        running: runtime.qrAuthRunning,
      });
    }

    if (req.method === 'GET' && url.pathname === '/qr-auth/code') {
      const runtime = getBootstrapRuntime();
      const qr = runtime.latestQR;
      if (!qr) {
        return json(res, 404, { error: 'No QR code yet. Call POST /qr-auth/start first.' });
      }
      return json(res, 200, {
        svg: qr.svg,
        tgUrl: qr.tgUrl,
        expiresAt: qr.expiresAt,
        expired: Date.now() > qr.expiresAt,
      });
    }

    if (req.method === 'POST' && url.pathname === '/download-media') {
      return handleDownloadMediaRoute(req, res, url);
    }

    if (req.method === 'GET' && url.pathname === '/custom-emoji') {
      return handleCustomEmojiRoute(req, res, url);
    }

    if (req.method === 'GET' && url.pathname === '/messages') {
      return handleMessagesRoute(req, res, url);
    }

    if (req.method === 'GET' && url.pathname === '/dialogs') {
      return handleDialogsRoute(req, res, url);
    }

    if (req.method === 'GET' && url.pathname === '/channels') {
      return handleChannelsRoute(req, res, url);
    }

    if (req.method === 'GET' && url.pathname === '/channel-posts') {
      return handleChannelPostsRoute(req, res, url);
    }

    if (req.method === 'POST' && url.pathname === '/auth/send-code') {
      const body = await parseBody(req);
      const { phoneNumber } = body;
      const requireAppDelivery = body.deliveryPreference === 'app';
      if (!phoneNumber) {
        return json(res, 400, { error: 'phoneNumber is required' });
      }

      if (body.tempSessionId) {
        await destroyTempRuntime(String(body.tempSessionId));
      }

      const authTransport = resolveTelegramAuthTransport(process.env, body.authTransport);
      const authClientProfile = resolvePhoneAuthClientProfile(body);
      const tempSessionId = generateTempSessionId();
      const runtime = getTempRuntime(tempSessionId);

      try {
        await ensurePhoneAuthTransport(runtime, {
          transport: authTransport,
          clientProfile: authClientProfile,
        });
        const result = await requestSentCode(runtime.client, phoneNumber);
        runtime.pendingPhoneCodeHash = result.phoneCodeHash;
        runtime.lastError = null;
        const tempSessionString = await snapshotTempAuthSession(runtime);
        const delivery = serializeSentCode(result, {
          preferredType: PREFERRED_LOGIN_DELIVERY_TYPE,
          authTransport,
          authClientProfile,
          attemptedDcIds: runtime.authAttemptedDcIds,
          connectedInitialDcId: runtime.authConnectedInitialDcId,
        });
        console.log(
          `[Phone Auth] sendCode delivery for ${runtimeLabel(runtime)}: type=${delivery.type || 'unknown'} preferred=${delivery.preferredType || 'none'} degraded=${delivery.degraded ? 'yes' : 'no'} next=${delivery.nextType || 'none'} timeout=${delivery.timeoutSeconds ?? 'none'} transport=${delivery.authTransport || 'none'} dc=${delivery.connectedInitialDcId || 'unknown'}`,
        );
        if (requireAppDelivery && delivery.type !== PREFERRED_LOGIN_DELIVERY_TYPE) {
          await cancelSentCode(runtime.client, phoneNumber, result.phoneCodeHash);
          runtime.pendingPhoneCodeHash = null;
          runtime.lastError = 'DEVICE_CODE_UNAVAILABLE';
          return json(res, 409, {
            error: 'DEVICE_CODE_UNAVAILABLE',
            code: 'DEVICE_CODE_UNAVAILABLE',
            message: 'Telegram did not offer app/device code delivery for this phone number.',
            phoneCodeHash: result.phoneCodeHash,
            tempSessionId,
            tempSessionString,
            preferredDeliveryType: PREFERRED_LOGIN_DELIVERY_TYPE,
            deliveryDegraded: delivery.degraded,
            deliveryDegradedReason: delivery.degradedReason,
            delivery,
          });
        }

        return json(res, 200, {
          phoneCodeHash: result.phoneCodeHash,
          tempSessionId,
          tempSessionString,
          preferredDeliveryType: PREFERRED_LOGIN_DELIVERY_TYPE,
          deliveryDegraded: delivery.degraded,
          deliveryDegradedReason: delivery.degradedReason,
          delivery,
        });
      } catch (err) {
        const normalized = normalizeTelegramPhoneAuthError(err);
        console.error(`[Phone Auth] sendCode failed for ${runtimeLabel(runtime)}:`, normalized.code, err.message);
        if (err.seconds) {
          res.setHeader('Retry-After', String(err.seconds));
          return json(res, 429, { error: 'Rate limited', retryAfter: err.seconds });
        }
        return json(res, 400, normalized);
      }
    }

    if (req.method === 'POST' && url.pathname === '/auth/resend-code') {
      const body = await parseBody(req);
      const tempSessionId = requireTempSessionId(res, body);
      if (!tempSessionId) return;
      const runtime = getTempRuntime(tempSessionId);
      const { phoneNumber, phoneCodeHash } = body;
      await restoreTempRuntimeFromBody(runtime, body);

      if (!phoneNumber) {
        return json(res, 400, { error: 'phoneNumber is required' });
      }

      const hash = phoneCodeHash || runtime.pendingPhoneCodeHash;
      if (!hash) {
        return json(res, 400, { error: 'No pending code. Call /auth/send-code first.' });
      }

      try {
        await ensureTransport(runtime, runtime.sessionString || '');
        const result = await runtime.client.invoke(
          new Api.auth.ResendCode({
            phoneNumber,
            phoneCodeHash: hash,
          }),
        );

        if (result instanceof Api.auth.SentCodeSuccess) {
          throw new Error('Already authorized');
        }

        runtime.pendingPhoneCodeHash = result.phoneCodeHash || hash;
        runtime.lastError = null;
        const tempSessionString = await snapshotTempAuthSession(runtime);
        const delivery = serializeSentCode(result);
        console.log(
          `[Phone Auth] resendCode delivery for ${runtimeLabel(runtime)}: type=${delivery.type || 'unknown'} next=${delivery.nextType || 'none'} timeout=${delivery.timeoutSeconds ?? 'none'}`,
        );
        return json(res, 200, {
          phoneCodeHash: runtime.pendingPhoneCodeHash,
          tempSessionId,
          tempSessionString,
          delivery,
        });
      } catch (err) {
        const normalized = normalizeTelegramPhoneAuthError(err);
        console.error(`[Phone Auth] resendCode failed for ${runtimeLabel(runtime)}:`, normalized.code, err.message);
        if (err.seconds) {
          res.setHeader('Retry-After', String(err.seconds));
          return json(res, 429, { error: 'Rate limited', retryAfter: err.seconds });
        }
        return json(res, 400, normalized);
      }
    }

    if (req.method === 'POST' && url.pathname === '/auth/sign-in') {
      const body = await parseBody(req);
      const tempSessionId = requireTempSessionId(res, body);
      if (!tempSessionId) return;
      const runtime = getTempRuntime(tempSessionId);
      const { phoneNumber, phoneCodeHash, phoneCode } = body;
      await restoreTempRuntimeFromBody(runtime, body);

      if (!phoneNumber || !phoneCode) {
        return json(res, 400, { error: 'phoneNumber and phoneCode are required' });
      }

      const hash = phoneCodeHash || runtime.pendingPhoneCodeHash;
      if (!hash) {
        return json(res, 400, { error: 'No pending code. Call /auth/send-code first.' });
      }

      try {
        await ensureTransport(runtime, runtime.sessionString || '');
        const result = await runtime.client.invoke(
          new Api.auth.SignIn({
            phoneNumber,
            phoneCodeHash: hash,
            phoneCode,
          }),
        );

        runtime.pendingPhoneCodeHash = null;
        runtime.connectionState = 'connected';
        runtime.reconnectAttempts = 0;
        runtime.lastError = null;
        runtime.latestMe = result.user;
        const tempSessionString = await snapshotTempAuthSession(runtime);

        return json(res, 200, {
          tempSessionId,
          tempSessionString,
          user: {
            userId: String(result.user.id),
            phone: result.user.phone ? `+${result.user.phone}` : phoneNumber,
            firstName: result.user.firstName || '',
            lastName: result.user.lastName || '',
          },
        });
      } catch (err) {
        if (err.errorMessage === 'SESSION_PASSWORD_NEEDED') {
          const tempSessionString = await snapshotTempAuthSession(runtime);
          return json(res, 200, { error: '2FA_REQUIRED', tempSessionId, tempSessionString });
        }
        runtime.lastError = err.message;
        console.error(`[Phone Auth] signIn failed for ${runtimeLabel(runtime)}:`, err.message);
        return json(res, 400, { error: err.message || 'Sign-in failed' });
      }
    }

    if (req.method === 'POST' && url.pathname === '/auth/check-password') {
      const body = await parseBody(req);
      const tempSessionId = requireTempSessionId(res, body);
      if (!tempSessionId) return;
      const runtime = getTempRuntime(tempSessionId);
      const { password } = body;
      await restoreTempRuntimeFromBody(runtime, body);

      if (!password) {
        return json(res, 400, { error: 'password is required' });
      }

      try {
        await ensureTransport(runtime, runtime.sessionString || '');
        const passwordState = await runtime.client.invoke(new Api.account.GetPassword());
        const srpCheck = await computeCheck(passwordState, password);
        const result = await runtime.client.invoke(new Api.auth.CheckPassword({ password: srpCheck }));

        runtime.pendingPhoneCodeHash = null;
        runtime.connectionState = 'connected';
        runtime.reconnectAttempts = 0;
        runtime.lastError = null;
        runtime.latestMe = result.user;
        const tempSessionString = await snapshotTempAuthSession(runtime);

        return json(res, 200, {
          tempSessionId,
          tempSessionString,
          user: {
            userId: String(result.user.id),
            phone: result.user.phone ? `+${result.user.phone}` : '',
            firstName: result.user.firstName || '',
            lastName: result.user.lastName || '',
          },
        });
      } catch (err) {
        runtime.lastError = err.message;
        console.error(
          `[Phone Auth] checkPassword failed for ${runtimeLabel(runtime)}:`,
          err.message,
        );
        return json(res, 400, { error: err.message || 'Wrong password' });
      }
    }

    if (req.method === 'POST' && url.pathname === '/sessions/register') {
      const body = await parseBody(req);
      const workspaceId = requireWorkspaceId(res, body, url);
      if (!workspaceId) return;
      const tempSessionId = requireTempSessionId(res, body);
      if (!tempSessionId) return;

      try {
        const tempRuntime = getTempRuntime(tempSessionId);
        await restoreTempRuntimeFromBody(tempRuntime, body);
        if (!tempRuntime.client && tempRuntime.sessionString) {
          await ensureTransport(tempRuntime, tempRuntime.sessionString);
          tempRuntime.connectionState = 'connected';
        }
        const result = await registerTempSessionToWorkspace(tempSessionId, workspaceId, body.userId || null);
        return json(res, 200, result);
      } catch (err) {
        console.error('[Phone Auth] register session failed:', err.message);
        return json(res, 409, { error: err.message || 'Failed to register session' });
      }
    }

    if (req.method === 'POST' && url.pathname === '/sessions/connect') {
      const body = await parseBody(req);
      const workspaceId = requireWorkspaceId(res, body, url);
      if (!workspaceId) return;

      try {
        const runtime = getWorkspaceRuntime(workspaceId);
        let connected = false;
        if (runtime.connectionState === 'connected' && runtime.client) {
          connected = await persistConnectedWorkspaceRuntime(runtime);
        } else {
          if (body.sessionString) {
            await sessionStore.saveSessionString(workspaceId, String(body.sessionString));
          }
          connected = await connectWorkspaceRuntime(workspaceId);
        }
        return json(res, 200, {
          workspaceId,
          state: connected ? 'connected' : 'disconnected',
        });
      } catch (err) {
        console.error('[Sessions] connect failed:', err.message);
        return json(res, 502, { error: err.message || 'Failed to connect session' });
      }
    }

    if (req.method === 'POST' && url.pathname === '/sessions/disconnect') {
      const body = await parseBody(req);
      const workspaceId = requireWorkspaceId(res, body, url);
      if (!workspaceId) return;
      const runtime = getWorkspaceRuntime(workspaceId);
      await destroyRuntimeClient(runtime);
      runtime.connectionState = 'disconnected';
      runtime.reconnectAttempts = 0;
      runtime.latestMe = null;
      runtime.pendingPhoneCodeHash = null;
      runtime.handlersRegistered = false;
      runtime.handlersRegisteredAt = null;
      runtime.catchUpScheduledAt = null;
      runtime.catchUpStartedAt = null;
      return json(res, 200, { disconnected: true, workspaceId });
    }

    if (req.method === 'POST' && url.pathname === '/auth/bind-workspace') {
      const body = await parseBody(req);
      const workspaceId = parseWorkspaceId(body.workspaceId);
      if (!workspaceId) {
        return json(res, 400, { error: 'workspaceId required' });
      }

      try {
        const result = await bindBootstrapToWorkspace(workspaceId, body.userId || null);
        return json(res, 200, result);
      } catch (err) {
        console.error('[Phone Auth] bind-workspace failed:', err.message);
        return json(res, 409, { error: err.message || 'Failed to bind workspace' });
      }
    }

    if (req.method === 'POST' && url.pathname === '/disconnect') {
      const body = await parseBody(req);
      const workspaceId = parseWorkspaceId(body.workspaceId);
      const runtime = workspaceId ? getWorkspaceRuntime(workspaceId) : getBootstrapRuntime();
      await destroyRuntimeClient(runtime);
      runtime.connectionState = 'disconnected';
      runtime.reconnectAttempts = 0;
      runtime.latestMe = null;
      runtime.pendingPhoneCodeHash = null;
      runtime.handlersRegistered = false;
      runtime.handlersRegisteredAt = null;
      runtime.catchUpScheduledAt = null;
      runtime.catchUpStartedAt = null;
      resetQrState(runtime);
      return json(res, 200, { disconnected: true, workspaceId: workspaceId || 0 });
    }

    return json(res, 404, { error: 'Not found' });
  } catch (err) {
    console.error('[HTTP] Error:', err);
    if (responseCommitted(res)) {
      return;
    }
    return json(res, 500, { error: err.message });
  }
});

const dialogSyncInterval = setInterval(() => {
  if (!SIDECAR_BACKGROUND_SYNC_ENABLED) return;
  for (const runtime of runtimeRegistry.list()) {
    if (!runtime.workspaceId) continue;
    if (runtime.connectionState !== 'connected') continue;
    syncDialogShells(runtime).catch((err) => {
      console.warn(`[Dialogs] Periodic sync failed for ${runtimeLabel(runtime)}:`, err.message);
    });
  }
}, DIALOG_SYNC_INTERVAL_MS);

// Ingest loop: stale source sync is degraded freshness, not live trigger
// failure. Re-run catch-up and gap repair in the background without tearing
// down the MTProto client that owns live inbound updates.
const INGEST_LIVENESS_INTERVAL_MS = 5_000;
const ingestLivenessInterval = setInterval(() => {
  for (const runtime of runtimeRegistry.list()) {
    const action = nextIngestAction(runtime);
    if (action === 'catch_up') {
      if (SIDECAR_BACKGROUND_SYNC_ENABLED) {
        scheduleCatchUp(runtime);
        scheduleGapRepair(runtime);
      }
    }
  }
}, INGEST_LIVENESS_INTERVAL_MS);

async function main() {
console.log(`[Sidecar] Starting on port ${PORT}`);
console.log(`[Sidecar] Backend callback: ${BACKEND_URL}`);
console.log(
  `[Sidecar] Telegram transport=${TELEGRAM_TRANSPORT}; phoneAuthTransport=${TELEGRAM_AUTH_TRANSPORT}; phoneAuthProfile=${TELEGRAM_AUTH_CLIENT_PROFILE}`,
);
console.log(`[Sidecar] Background sync enabled=${SIDECAR_BACKGROUND_SYNC_ENABLED}`);
console.log(`[Sidecar] Media hydration enabled=${SIDECAR_MEDIA_HYDRATION_ENABLED}`);
console.log('[Sidecar] Background sync uses the connected runtime client');
console.log(`[Sidecar] Dialog sync interval=${DIALOG_SYNC_INTERVAL_MS}ms`);

  if (!API_ID || !API_HASH) {
    console.error('[Sidecar] TELEGRAM_API_ID and TELEGRAM_API_HASH are required');
    process.exit(1);
  }

  await withStartupRetry(
    'sidecar database schemas',
    async () => {
      await eventOutbox.ensureSchema();
      await sendIdempotencyCache.ensureSchema();
    },
    {
      timeoutMs: DB_STARTUP_TIMEOUT_MS,
      onRetry: ({ attempt, delayMs, error }) => {
        console.warn(
          `[Sidecar] Database not ready during startup (${error.code || error.message}); retry ${attempt} in ${delayMs}ms`,
        );
      },
    },
  );
  eventOutbox.start();

  server.listen(PORT, () => {
    console.log(`[Sidecar] HTTP API ready at http://localhost:${PORT}`);
  });

  restoreStoredSessions().catch((err) => {
    console.error('[Sidecar] Stored session restore failed:', err);
  });
}

async function shutdown(signal) {
  console.log(`[Sidecar] ${signal} received, shutting down...`);
  eventOutbox.stop();
  clearInterval(dialogSyncInterval);
  clearInterval(ingestLivenessInterval);

  for (const runtime of runtimeRegistry.list()) {
    try {
      await destroyRuntimeClient(runtime);
    } catch {}
  }

  await pool.end();

  server.close(() => {
    process.exit(0);
  });
}

process.on('SIGTERM', () => {
  shutdown('SIGTERM').catch((err) => {
    console.error('[Sidecar] Shutdown failed:', err);
    process.exit(1);
  });
});

process.on('SIGINT', () => {
  shutdown('SIGINT').catch((err) => {
    console.error('[Sidecar] Shutdown failed:', err);
    process.exit(1);
  });
});

main().catch((err) => {
  console.error('[Sidecar] Fatal:', err);
  process.exit(1);
});
