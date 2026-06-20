export const CREATE_DURABLE_TELEGRAM_STATE_SQL = `
CREATE TABLE IF NOT EXISTS telegram_sidecar_peers (
  workspace_id BIGINT NOT NULL,
  peer_id TEXT NOT NULL,
  peer_kind TEXT NOT NULL,
  access_hash TEXT,
  display_name TEXT,
  username TEXT,
  phone TEXT,
  flags JSONB NOT NULL DEFAULT '{}'::jsonb,
  source TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, peer_id, peer_kind)
);
CREATE TABLE IF NOT EXISTS telegram_sidecar_dialogs (
  workspace_id BIGINT NOT NULL,
  chat_id TEXT NOT NULL,
  dialog_type TEXT NOT NULL,
  title TEXT,
  unread_count INTEGER NOT NULL DEFAULT 0,
  top_message_id TEXT,
  last_message_text TEXT NOT NULL DEFAULT '',
  last_message_date BIGINT,
  last_message_is_outgoing BOOLEAN NOT NULL DEFAULT false,
  input_peer JSONB NOT NULL DEFAULT '{}'::jsonb,
  source TEXT NOT NULL,
  synced_at DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_telegram_sidecar_dialogs_workspace_updated
  ON telegram_sidecar_dialogs (workspace_id, updated_at);
CREATE TABLE IF NOT EXISTS telegram_sidecar_messages (
  workspace_id BIGINT NOT NULL,
  chat_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  sender_id TEXT,
  message_date BIGINT,
  text TEXT NOT NULL DEFAULT '',
  is_outgoing BOOLEAN NOT NULL DEFAULT false,
  media_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
  source TEXT NOT NULL,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_telegram_sidecar_messages_workspace_chat_date
  ON telegram_sidecar_messages (workspace_id, chat_id, message_date);
CREATE TABLE IF NOT EXISTS telegram_sidecar_media_refs (
  workspace_id BIGINT NOT NULL,
  chat_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  media_key TEXT NOT NULL,
  media_kind TEXT NOT NULL,
  document_id TEXT,
  photo_id TEXT,
  mime_type TEXT,
  size BIGINT,
  source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  queued_at DOUBLE PRECISION,
  hydrated_at DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, chat_id, message_id, media_key)
);
CREATE INDEX IF NOT EXISTS idx_telegram_sidecar_media_refs_pending
  ON telegram_sidecar_media_refs (workspace_id, status, updated_at);
CREATE TABLE IF NOT EXISTS telegram_sidecar_update_cursors (
  workspace_id BIGINT NOT NULL,
  cursor_scope TEXT NOT NULL,
  channel_id TEXT NOT NULL DEFAULT '',
  pts BIGINT,
  seq BIGINT,
  qts BIGINT,
  telegram_date BIGINT,
  degraded_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at DOUBLE PRECISION,
  applied_at DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, cursor_scope, channel_id)
);
`;

function asString(value) {
  if (value == null) return null;
  return String(value);
}

function asNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compactObject(value) {
  return Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined && entry !== null),
  );
}

function safeJson(value) {
  return JSON.stringify(value, (_key, entry) => (
    typeof entry === 'bigint' ? entry.toString() : entry
  ));
}

function safeJsonObject(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function displayName(peer) {
  return [
    peer?.firstName,
    peer?.lastName,
  ].filter(Boolean).join(' ') || peer?.title || peer?.username || null;
}

function peerFlags(peer) {
  return compactObject({
    bot: peer?.bot === undefined ? undefined : Boolean(peer.bot),
    self: peer?.self === undefined ? undefined : Boolean(peer.self),
    support: peer?.support === undefined ? undefined : Boolean(peer.support),
    deleted: peer?.deleted === undefined ? undefined : Boolean(peer.deleted),
    broadcast: peer?.broadcast === undefined ? undefined : Boolean(peer.broadcast),
    megagroup: peer?.megagroup === undefined ? undefined : Boolean(peer.megagroup),
  });
}

function mediaRef(msg) {
  const media = msg?.media;
  if (!media) return {};
  const document = msg.document || media.document;
  const photo = msg.photo || media.photo;
  return compactObject({
    className: media.className,
    documentId: asString(document?.id),
    photoId: asString(photo?.id),
    mimeType: document?.mimeType,
    size: document?.size == null ? null : Number(document.size),
  });
}

function mediaRefKey(ref, messageId) {
  if (!ref?.className) return null;
  if (ref.documentId) return `document:${ref.documentId}`;
  if (ref.photoId) return `photo:${ref.photoId}`;
  return `message:${messageId}`;
}

function inputPeerRef(dialog) {
  const inputPeer = dialog?.inputPeer || dialog?.inputEntity || dialog?.entity?.inputPeer || null;
  if (!inputPeer) return {};
  return compactObject({
    className: inputPeer.className,
    userId: asString(inputPeer.userId),
    chatId: asString(inputPeer.chatId),
    channelId: asString(inputPeer.channelId),
    accessHash: asString(inputPeer.accessHash),
  });
}

function rawMessageShape(msg) {
  return compactObject({
    id: asString(msg?.id),
    chatId: asString(msg?.chatId || msg?.peerId),
    senderId: asString(msg?.senderId || msg?.fromId || msg?.chatId || msg?.peerId),
    date: msg?.date,
    editDate: msg?.editDate,
    out: Boolean(msg?.out),
    mediaClassName: msg?.media?.className,
  });
}

function channelCursorIdForMessage(msg) {
  const peerChannelId = asString(msg?.peerId?.channelId);
  if (peerChannelId) return peerChannelId;
  const chat = msg?.chat || msg?._chat;
  if (chat?.broadcast || chat?.megagroup || chat?.gigagroup) {
    return asString(chat.id || msg?.chatId) || '';
  }
  return '';
}

function incrementRuntimeDurableState(runtime, field) {
  if (!runtime) return;
  if (!runtime.telegramDurableStateCounts) {
    runtime.telegramDurableStateCounts = {
      peers: 0,
      dialogs: 0,
      messages: 0,
      mediaRefs: 0,
      cursors: 0,
    };
  }
  runtime.telegramDurableStateCounts[field] = (runtime.telegramDurableStateCounts[field] || 0) + 1;
}

function rowToMediaRef(row) {
  const updatedAt = row.updated_at ? Date.parse(row.updated_at) / 1000 : null;
  return {
    workspaceId: asNumber(row.workspace_id),
    chatId: asString(row.chat_id),
    messageId: asString(row.message_id),
    mediaKey: row.media_key,
    mediaKind: row.media_kind,
    documentId: row.document_id ?? null,
    photoId: row.photo_id ?? null,
    mimeType: row.mime_type ?? null,
    size: asNumber(row.size),
    source: row.source,
    status: row.status,
    attempts: asNumber(row.attempts) ?? 0,
    lastError: row.last_error ?? null,
    queuedAt: asNumber(row.queued_at),
    hydratedAt: asNumber(row.hydrated_at),
    ...(updatedAt == null ? {} : { updatedAt }),
  };
}

function inputPeerRefFromPeerRow(row) {
  const peerId = asString(row?.peer_id);
  if (!peerId) return null;
  const accessHash = asString(row.access_hash);
  const flags = safeJsonObject(row.flags);
  if (row.peer_kind === 'user' && accessHash) {
    return {
      className: 'InputPeerUser',
      userId: peerId,
      accessHash,
      source: row.source || 'peer_cache',
    };
  }
  if (row.peer_kind === 'chat' && (flags.broadcast || flags.megagroup) && accessHash) {
    return {
      className: 'InputPeerChannel',
      channelId: peerId,
      accessHash,
      source: row.source || 'peer_cache',
    };
  }
  if (row.peer_kind === 'chat' && accessHash && !peerId.startsWith('-')) {
    return {
      className: 'InputPeerUser',
      userId: peerId,
      accessHash,
      source: row.source || 'peer_cache_legacy_private_chat',
    };
  }
  if (row.peer_kind === 'chat') {
    return {
      className: 'InputPeerChat',
      chatId: peerId,
      source: row.source || 'peer_cache',
    };
  }
  return null;
}

export function createTelegramDurableStateStore({ pool } = {}) {
  if (!pool) {
    throw new Error('pool is required');
  }

  let schemaReady = null;

  async function ensureSchema() {
    if (!schemaReady) {
      schemaReady = pool.query(CREATE_DURABLE_TELEGRAM_STATE_SQL).catch((err) => {
        schemaReady = null;
        throw err;
      });
    }
    await schemaReady;
  }

  async function rememberPeer(
    workspaceId,
    peer,
    source = 'live',
    receivedAt = null,
    peerKind = null,
    runtime = null,
  ) {
    const peerId = asString(peer?.id);
    if (!workspaceId || !peerId) return false;

    await ensureSchema();
    const kind = peerKind || (peer?.title || peer?.broadcast || peer?.megagroup ? 'chat' : 'user');
    await pool.query(
      `INSERT INTO telegram_sidecar_peers (
         workspace_id, peer_id, peer_kind, access_hash, display_name,
         username, phone, flags, source, updated_at
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, TO_TIMESTAMP($10))
       ON CONFLICT (workspace_id, peer_id, peer_kind)
       DO UPDATE SET
         access_hash = COALESCE(EXCLUDED.access_hash, telegram_sidecar_peers.access_hash),
         display_name = COALESCE(EXCLUDED.display_name, telegram_sidecar_peers.display_name),
         username = COALESCE(EXCLUDED.username, telegram_sidecar_peers.username),
         phone = COALESCE(EXCLUDED.phone, telegram_sidecar_peers.phone),
         flags = telegram_sidecar_peers.flags || EXCLUDED.flags,
         source = EXCLUDED.source,
         updated_at = EXCLUDED.updated_at`,
      [
        workspaceId,
        peerId,
        kind,
        asString(peer.accessHash),
        displayName(peer),
        peer.username || null,
        peer.phone || null,
        safeJson(peerFlags(peer)),
        source,
        receivedAt || Date.now() / 1000,
      ],
    );
    incrementRuntimeDurableState(runtime, 'peers');
    return true;
  }

  async function rememberMessage({
    runtime,
    msg,
    source = 'live',
    receivedAt = null,
  } = {}) {
    const workspaceId = runtime?.workspaceId;
    const chatId = asString(msg?.chatId || msg?.peerId);
    const messageId = asString(msg?.id);
    if (!workspaceId || !chatId || !messageId) return false;

    await ensureSchema();
    const ref = mediaRef(msg);
    await pool.query(
      `INSERT INTO telegram_sidecar_messages (
         workspace_id, chat_id, message_id, sender_id, message_date, text,
         is_outgoing, media_ref, source, raw, received_at, updated_at
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb, $11, NOW())
       ON CONFLICT (workspace_id, chat_id, message_id)
       DO UPDATE SET
         sender_id = COALESCE(EXCLUDED.sender_id, telegram_sidecar_messages.sender_id),
         message_date = COALESCE(EXCLUDED.message_date, telegram_sidecar_messages.message_date),
         text = EXCLUDED.text,
         is_outgoing = EXCLUDED.is_outgoing,
         media_ref = EXCLUDED.media_ref,
         source = EXCLUDED.source,
         raw = telegram_sidecar_messages.raw || EXCLUDED.raw,
         received_at = COALESCE(EXCLUDED.received_at, telegram_sidecar_messages.received_at),
         updated_at = NOW()`,
      [
        workspaceId,
        chatId,
        messageId,
        asString(msg.senderId || msg.fromId || chatId),
        msg.date || null,
        msg.message || '',
        Boolean(msg.out),
        safeJson(ref),
        source,
        safeJson(rawMessageShape(msg)),
        receivedAt,
      ],
    );
    const key = mediaRefKey(ref, messageId);
    if (key) {
      await pool.query(
        `INSERT INTO telegram_sidecar_media_refs (
           workspace_id, chat_id, message_id, media_key, media_kind,
           document_id, photo_id, mime_type, size, source, status,
           attempts, last_error, queued_at, hydrated_at, updated_at
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending', 0, NULL, $11, NULL, NOW())
         ON CONFLICT (workspace_id, chat_id, message_id, media_key)
         DO UPDATE SET
           media_kind = EXCLUDED.media_kind,
           document_id = COALESCE(EXCLUDED.document_id, telegram_sidecar_media_refs.document_id),
           photo_id = COALESCE(EXCLUDED.photo_id, telegram_sidecar_media_refs.photo_id),
           mime_type = COALESCE(EXCLUDED.mime_type, telegram_sidecar_media_refs.mime_type),
           size = COALESCE(EXCLUDED.size, telegram_sidecar_media_refs.size),
           source = EXCLUDED.source,
           status = CASE
             WHEN telegram_sidecar_media_refs.status = 'hydrated'
             THEN telegram_sidecar_media_refs.status
             ELSE 'pending'
           END,
           queued_at = COALESCE(telegram_sidecar_media_refs.queued_at, EXCLUDED.queued_at),
           updated_at = NOW()`,
        [
          workspaceId,
          chatId,
          messageId,
          key,
          ref.className,
          ref.documentId || null,
          ref.photoId || null,
          ref.mimeType || null,
          ref.size ?? null,
          source,
          receivedAt,
        ],
      );
      incrementRuntimeDurableState(runtime, 'mediaRefs');
    }
    incrementRuntimeDurableState(runtime, 'messages');
    return true;
  }

  async function rememberDialogState({
    runtime,
    dialogs = [],
    source = 'dialog_sync',
    syncedAt = null,
  } = {}) {
    const workspaceId = runtime?.workspaceId;
    if (!workspaceId || !Array.isArray(dialogs) || !dialogs.length) {
      return 0;
    }

    await ensureSchema();
    let saved = 0;
    for (const dialog of dialogs) {
      const chatId = asString(dialog.chatId || dialog.id);
      if (!chatId) continue;
      await pool.query(
        `INSERT INTO telegram_sidecar_dialogs (
           workspace_id, chat_id, dialog_type, title, unread_count,
           top_message_id, last_message_text, last_message_date,
           last_message_is_outgoing, input_peer, source, synced_at, updated_at
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, NOW())
         ON CONFLICT (workspace_id, chat_id)
         DO UPDATE SET
           dialog_type = EXCLUDED.dialog_type,
           title = COALESCE(EXCLUDED.title, telegram_sidecar_dialogs.title),
           unread_count = EXCLUDED.unread_count,
           top_message_id = COALESCE(EXCLUDED.top_message_id, telegram_sidecar_dialogs.top_message_id),
           last_message_text = EXCLUDED.last_message_text,
           last_message_date = COALESCE(EXCLUDED.last_message_date, telegram_sidecar_dialogs.last_message_date),
           last_message_is_outgoing = EXCLUDED.last_message_is_outgoing,
           input_peer = telegram_sidecar_dialogs.input_peer || EXCLUDED.input_peer,
           source = EXCLUDED.source,
           synced_at = COALESCE(EXCLUDED.synced_at, telegram_sidecar_dialogs.synced_at),
           updated_at = NOW()`,
        [
          workspaceId,
          chatId,
          dialog.type || (dialog.isUser ? 'private' : 'unknown'),
          dialog.title || dialog.name || displayName(dialog.entity) || null,
          Number(dialog.unreadCount || 0),
          asString(dialog.topMessageId || dialog.message?.id),
          dialog.lastMessageText ?? dialog.message?.message ?? '',
          dialog.lastMessageDate ?? dialog.message?.date ?? null,
          dialog.lastMessageIsOutgoing ?? Boolean(dialog.message?.out),
          safeJson(inputPeerRef(dialog)),
          source,
          syncedAt,
        ],
      );
      incrementRuntimeDurableState(runtime, 'dialogs');
      saved += 1;
    }
    return saved;
  }

  async function rememberUpdateCursorState({
    runtime,
    cursorScope = 'hot_path',
    channelId = '',
    pts = null,
    seq = null,
    qts = null,
    telegramDate = null,
    degradedState = {},
    receivedAt = null,
    appliedAt = null,
  } = {}) {
    const workspaceId = runtime?.workspaceId;
    if (!workspaceId) return false;

    await ensureSchema();
    await pool.query(
      `INSERT INTO telegram_sidecar_update_cursors (
         workspace_id, cursor_scope, channel_id, pts, seq, qts,
         telegram_date, degraded_state, received_at, applied_at, updated_at
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, NOW())
       ON CONFLICT (workspace_id, cursor_scope, channel_id)
       DO UPDATE SET
         pts = COALESCE(EXCLUDED.pts, telegram_sidecar_update_cursors.pts),
         seq = COALESCE(EXCLUDED.seq, telegram_sidecar_update_cursors.seq),
         qts = COALESCE(EXCLUDED.qts, telegram_sidecar_update_cursors.qts),
         telegram_date = COALESCE(EXCLUDED.telegram_date, telegram_sidecar_update_cursors.telegram_date),
         degraded_state = EXCLUDED.degraded_state,
         received_at = COALESCE(EXCLUDED.received_at, telegram_sidecar_update_cursors.received_at),
         applied_at = COALESCE(EXCLUDED.applied_at, telegram_sidecar_update_cursors.applied_at),
         updated_at = NOW()`,
      [
        workspaceId,
        cursorScope,
        asString(channelId) || '',
        pts,
        seq,
        qts,
        telegramDate,
        safeJson(degradedState),
        receivedAt,
        appliedAt,
      ],
    );
    incrementRuntimeDurableState(runtime, 'cursors');
    return true;
  }

  async function rememberUpdateCursor({
    runtime,
    msg,
    receivedAt = null,
    appliedAt = null,
  } = {}) {
    return rememberUpdateCursorState({
      runtime,
      cursorScope: 'hot_path',
      channelId: channelCursorIdForMessage(msg),
      pts: msg?.pts || null,
      seq: msg?.seq || null,
      qts: msg?.qts || null,
      telegramDate: msg?.date || null,
      degradedState: {},
      receivedAt,
      appliedAt,
    });
  }

  async function findInputPeerRef(workspaceId, chatId) {
    const normalizedWorkspaceId = asNumber(workspaceId);
    const normalizedChatId = asString(chatId);
    if (!normalizedWorkspaceId || !normalizedChatId) {
      return null;
    }

    await ensureSchema();
    const dialogResult = await pool.query(
      `SELECT input_peer, source
       FROM telegram_sidecar_dialogs
       WHERE workspace_id = $1
         AND chat_id = $2
         AND input_peer <> '{}'::jsonb
       LIMIT 1`,
      [normalizedWorkspaceId, normalizedChatId],
    );
    const dialogRow = dialogResult.rows?.[0];
    const dialogRef = safeJsonObject(dialogRow?.input_peer);
    if (dialogRef?.className) {
      return {
        ...dialogRef,
        source: dialogRow.source || 'dialog_cache',
      };
    }

    const peerResult = await pool.query(
      `SELECT peer_id, peer_kind, access_hash, flags, source
       FROM telegram_sidecar_peers
       WHERE workspace_id = $1
         AND peer_id = $2
       ORDER BY CASE peer_kind
         WHEN 'user' THEN 0
         WHEN 'chat' THEN 1
         ELSE 2
       END
       LIMIT 4`,
      [normalizedWorkspaceId, normalizedChatId],
    );
    for (const row of peerResult.rows || []) {
      const ref = inputPeerRefFromPeerRow(row);
      if (ref) return ref;
    }
    return null;
  }

  async function rememberHotMessageState({
    runtime,
    msg,
    source = 'live',
    receivedAt = null,
    appliedAt = null,
    resolvedPeer = null,
  } = {}) {
    const workspaceId = runtime?.workspaceId;
    if (!workspaceId || !msg) return false;

    // Cold gramjs cache (first-contact customer, or right after a restart)
    // leaves msg.chat/msg.sender empty. The caller already resolved the entity
    // for the human filter — persist it so the first reply can resolve its
    // InputPeer from the durable cache instead of throwing (#417).
    const peerFallback = msg.isPrivate ? resolvedPeer : null;
    await Promise.all([
      rememberPeer(workspaceId, msg.chat || msg._chat || peerFallback, source, receivedAt, 'chat', runtime),
      rememberPeer(
        workspaceId,
        msg.sender || msg._sender || (msg.out ? null : peerFallback),
        source,
        receivedAt,
        'user',
        runtime,
      ),
    ]);
    await rememberMessage({ runtime, msg, source, receivedAt });
    await rememberUpdateCursor({ runtime, msg, receivedAt, appliedAt });
    return true;
  }

  async function summaryForWorkspace(workspaceId) {
    if (!workspaceId) {
      return { peers: 0, dialogs: 0, messages: 0, mediaRefs: 0, cursors: 0 };
    }

    await ensureSchema();
    const result = await pool.query(
      `SELECT
         (SELECT COUNT(*)::int FROM telegram_sidecar_peers WHERE workspace_id = $1) AS peers,
         (SELECT COUNT(*)::int FROM telegram_sidecar_dialogs WHERE workspace_id = $1) AS dialogs,
         (SELECT COUNT(*)::int FROM telegram_sidecar_messages WHERE workspace_id = $1) AS messages,
         (SELECT COUNT(*)::int FROM telegram_sidecar_media_refs WHERE workspace_id = $1) AS media_refs,
         (SELECT COUNT(*)::int FROM telegram_sidecar_update_cursors WHERE workspace_id = $1) AS cursors`,
      [workspaceId],
    );
    const row = result.rows?.[0] || {};
    return {
      peers: Number(row.peers || 0),
      dialogs: Number(row.dialogs || 0),
      messages: Number(row.messages || 0),
      mediaRefs: Number(row.media_refs || 0),
      cursors: Number(row.cursors || 0),
    };
  }

  async function listPendingMediaRefs(workspaceId, { limit = 100 } = {}) {
    if (!workspaceId) {
      return [];
    }

    await ensureSchema();
    const result = await pool.query(
      `SELECT
         workspace_id,
         chat_id,
         message_id,
         media_key,
         media_kind,
         document_id,
         photo_id,
         mime_type,
         size,
         source,
         status,
         attempts,
         last_error,
         queued_at,
         hydrated_at,
         updated_at
       FROM telegram_sidecar_media_refs
       WHERE workspace_id = $1
         AND status IN ('pending', 'failed')
       ORDER BY updated_at ASC
       LIMIT $2`,
      [workspaceId, limit],
    );
    return (result.rows || []).map(rowToMediaRef);
  }

  async function markMediaRefHydrated(ref, { hydratedAt = Date.now() / 1000 } = {}) {
    if (!ref?.workspaceId || !ref.chatId || !ref.messageId || !ref.mediaKey) {
      return false;
    }

    await ensureSchema();
    await pool.query(
      `UPDATE telegram_sidecar_media_refs
       SET status = 'hydrated',
           last_error = NULL,
           hydrated_at = $1,
           updated_at = NOW()
       WHERE workspace_id = $2
         AND chat_id = $3
         AND message_id = $4
         AND media_key = $5`,
      [hydratedAt, ref.workspaceId, ref.chatId, ref.messageId, ref.mediaKey],
    );
    return true;
  }

  async function markMediaRefFailed(ref, err) {
    if (!ref?.workspaceId || !ref.chatId || !ref.messageId || !ref.mediaKey) {
      return false;
    }

    await ensureSchema();
    await pool.query(
      `UPDATE telegram_sidecar_media_refs
       SET status = 'failed',
           attempts = attempts + 1,
           last_error = $1,
           updated_at = NOW()
       WHERE workspace_id = $2
         AND chat_id = $3
         AND message_id = $4
         AND media_key = $5`,
      [err?.message || String(err), ref.workspaceId, ref.chatId, ref.messageId, ref.mediaKey],
    );
    return true;
  }

  async function cursorFreshnessForWorkspace(
    workspaceId,
    { now = Date.now() / 1000, staleAfterSeconds = 300 } = {},
  ) {
    if (!workspaceId) {
      return {
        latestReceivedAt: null,
        latestAppliedAt: null,
        maxAgeSeconds: null,
        stale: false,
        cursors: [],
      };
    }

    await ensureSchema();
    const result = await pool.query(
      `SELECT
         cursor_scope,
         channel_id,
         pts,
         seq,
         qts,
         telegram_date,
         received_at,
         applied_at,
         degraded_state
       FROM telegram_sidecar_update_cursors
       WHERE workspace_id = $1
       ORDER BY cursor_scope ASC, channel_id ASC`,
      [workspaceId],
    );
    const cursors = (result.rows || []).map((row) => {
      const receivedAt = asNumber(row.received_at);
      const appliedAt = asNumber(row.applied_at);
      const ageSeconds = appliedAt == null ? null : Math.max(0, Math.round(now - appliedAt));
      const stale = ageSeconds == null || ageSeconds > staleAfterSeconds;
      return {
        scope: row.cursor_scope || '',
        channelId: row.channel_id || '',
        pts: asNumber(row.pts),
        seq: asNumber(row.seq),
        qts: asNumber(row.qts),
        telegramDate: asNumber(row.telegram_date),
        receivedAt,
        appliedAt,
        ageSeconds,
        stale,
        degradedState: safeJsonObject(row.degraded_state),
      };
    });
    const receivedTimes = cursors
      .map((cursor) => cursor.receivedAt)
      .filter((value) => value != null);
    const appliedTimes = cursors
      .map((cursor) => cursor.appliedAt)
      .filter((value) => value != null);
    const ages = cursors.map((cursor) => cursor.ageSeconds).filter((value) => value != null);
    return {
      latestReceivedAt: receivedTimes.length ? Math.max(...receivedTimes) : null,
      latestAppliedAt: appliedTimes.length ? Math.max(...appliedTimes) : null,
      maxAgeSeconds: ages.length ? Math.max(...ages) : null,
      stale: cursors.some((cursor) => cursor.stale),
      cursors,
    };
  }

  async function pruneLegacyPrivateHotPathCursors(workspaceId) {
    if (!workspaceId) return 0;

    await ensureSchema();
    const result = await pool.query(
      `DELETE FROM telegram_sidecar_update_cursors cursor_row
       WHERE cursor_row.workspace_id = $1
         AND cursor_row.cursor_scope = 'hot_path'
         AND cursor_row.channel_id <> ''
         AND NOT EXISTS (
           SELECT 1
           FROM telegram_sidecar_peers peer_row
           WHERE peer_row.workspace_id = cursor_row.workspace_id
             AND peer_row.peer_id = cursor_row.channel_id
             AND peer_row.peer_kind = 'chat'
             AND (
               COALESCE((peer_row.flags->>'broadcast')::boolean, false)
               OR COALESCE((peer_row.flags->>'megagroup')::boolean, false)
               OR COALESCE((peer_row.flags->>'gigagroup')::boolean, false)
             )
         )`,
      [workspaceId],
    );
    return result.rowCount || 0;
  }

  return {
    cursorFreshnessForWorkspace,
    ensureSchema,
    findInputPeerRef,
    listPendingMediaRefs,
    markMediaRefFailed,
    markMediaRefHydrated,
    pruneLegacyPrivateHotPathCursors,
    rememberDialogState,
    rememberHotMessageState,
    rememberMessage,
    rememberPeer,
    rememberUpdateCursor,
    rememberUpdateCursorState,
    summaryForWorkspace,
  };
}
