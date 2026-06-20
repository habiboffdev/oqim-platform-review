const DEFAULT_SEEN_LIMIT = 5000;

function asString(value) {
  if (value == null) return null;
  return String(value);
}

function asNumber(value) {
  if (value == null || value === '') return null;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value.toJSNumber === 'function') return value.toJSNumber();
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function ensureConnectorRuntime(runtime) {
  if (!runtime.telegramConnector) {
    runtime.telegramConnector = {
      seenMessages: new Map(),
      cursors: new Map(),
      duplicatesSkipped: 0,
      gapsDetected: 0,
      lastGapAt: null,
      lastGap: null,
      lastDecision: null,
    };
  }
  return runtime.telegramConnector;
}

function rememberSeen(connector, key, now) {
  connector.seenMessages.set(key, now);
  while (connector.seenMessages.size > DEFAULT_SEEN_LIMIT) {
    const oldestKey = connector.seenMessages.keys().next().value;
    connector.seenMessages.delete(oldestKey);
  }
}

function cachedChat(msg) {
  return msg?.chat || msg?._chat || null;
}

function isChannelLikeMessage(msg) {
  const peerChannelId = asString(msg?.peerId?.channelId);
  if (peerChannelId) return true;
  const chat = cachedChat(msg);
  return Boolean(chat?.broadcast || chat?.megagroup || chat?.gigagroup);
}

export function channelCursorIdForMessage(msg) {
  if (!isChannelLikeMessage(msg)) return '';
  return asString(msg?.peerId?.channelId || cachedChat(msg)?.id || msg?.chatId || '') || '';
}

export function connectorMessageKey(runtime, msg) {
  const workspaceId = asString(runtime?.workspaceId);
  const chatId = asString(msg?.chatId || msg?.peerId);
  const messageId = asString(msg?.id);
  if (!workspaceId || !chatId || !messageId) return null;
  return `${workspaceId}:${chatId}:${messageId}`;
}

export function connectorCursorForMessage(msg) {
  const channelId = channelCursorIdForMessage(msg);
  return {
    scope: 'hot_path',
    channelId,
    key: `${channelId || 'global'}`,
    pts: asNumber(msg?.pts),
    seq: asNumber(msg?.seq),
    qts: asNumber(msg?.qts),
    telegramDate: asNumber(msg?.date),
  };
}

function cursorAdvancedByGap(previous, current) {
  if (!previous?.pts || !current?.pts) return false;
  // GramJS Message objects do not reliably expose pts_count. Treat a jump of
  // more than one as a connector repair signal, but still let the current live
  // message through so customer replies are not delayed by history repair.
  return current.pts > previous.pts + 1;
}

export function prepareInboundConnectorEvent({
  runtime,
  msg,
  source = 'live',
  isHistorical = false,
  nowSeconds = Date.now() / 1000,
  scheduleGapRepair = null,
} = {}) {
  if (!runtime?.workspaceId || !msg?.id) {
    return { action: 'skip', reason: 'malformed' };
  }

  const connector = ensureConnectorRuntime(runtime);
  const messageKey = connectorMessageKey(runtime, msg);
  if (!messageKey) {
    return { action: 'skip', reason: 'missing_message_key' };
  }

  if (connector.seenMessages.has(messageKey)) {
    connector.duplicatesSkipped += 1;
    connector.lastDecision = {
      action: 'skip',
      reason: 'duplicate_in_runtime',
      messageKey,
      at: nowSeconds,
    };
    return {
      action: 'skip',
      reason: 'duplicate_in_runtime',
      telemetry: {
        telegram_connector_decision: 'duplicate_skip',
        telegram_connector_message_key: messageKey,
      },
    };
  }

  rememberSeen(connector, messageKey, nowSeconds);
  const cursor = connectorCursorForMessage(msg);
  const previousCursor = connector.cursors.get(cursor.key) || null;
  let gapDetected = false;
  if (!isHistorical && cursorAdvancedByGap(previousCursor, cursor)) {
    gapDetected = true;
    connector.gapsDetected += 1;
    connector.lastGapAt = new Date(nowSeconds * 1000).toISOString();
    connector.lastGap = {
      cursorKey: cursor.key,
      previousPts: previousCursor.pts,
      currentPts: cursor.pts,
      channelId: cursor.channelId,
      messageKey,
    };
    scheduleGapRepair?.(runtime, 0);
  }

  const mergedCursor = {
    ...previousCursor,
    ...cursor,
    pts: Math.max(previousCursor?.pts || 0, cursor.pts || 0) || cursor.pts,
    seq: Math.max(previousCursor?.seq || 0, cursor.seq || 0) || cursor.seq,
    qts: Math.max(previousCursor?.qts || 0, cursor.qts || 0) || cursor.qts,
    telegramDate: Math.max(previousCursor?.telegramDate || 0, cursor.telegramDate || 0)
      || cursor.telegramDate,
    lastSource: source,
    lastSeenAt: nowSeconds,
  };
  connector.cursors.set(cursor.key, mergedCursor);

  connector.lastDecision = {
    action: 'forward',
    reason: gapDetected ? 'forward_with_gap_repair' : 'forward',
    messageKey,
    at: nowSeconds,
  };

  return {
    action: 'forward',
    reason: gapDetected ? 'forward_with_gap_repair' : 'forward',
    messageKey,
    cursor,
    gapDetected,
    telemetry: {
      telegram_connector_decision: gapDetected ? 'forward_with_gap_repair' : 'forward',
      telegram_connector_message_key: messageKey,
      telegram_connector_cursor_scope: cursor.scope,
      telegram_connector_cursor_channel_id: cursor.channelId || null,
      telegram_connector_gap_detected: gapDetected,
      telegram_connector_previous_pts: previousCursor?.pts ?? null,
      telegram_connector_current_pts: cursor.pts ?? null,
    },
  };
}

export function connectorRuntimeStatus(runtime) {
  const connector = runtime?.telegramConnector;
  if (!connector) {
    return {
      seenMessages: 0,
      cursors: 0,
      duplicatesSkipped: 0,
      gapsDetected: 0,
      lastGapAt: null,
      lastGap: null,
      lastDecision: null,
    };
  }
  return {
    seenMessages: connector.seenMessages?.size || 0,
    cursors: connector.cursors?.size || 0,
    duplicatesSkipped: connector.duplicatesSkipped || 0,
    gapsDetected: connector.gapsDetected || 0,
    lastGapAt: connector.lastGapAt || null,
    lastGap: connector.lastGap || null,
    lastDecision: connector.lastDecision || null,
  };
}
