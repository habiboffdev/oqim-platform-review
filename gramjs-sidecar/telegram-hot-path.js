import {
  displayNameForMessage,
  isPrivateHumanEntity,
} from './chat-filters.js';
import { buildInboundEvent } from './telegram-codec.js';

export function nowSeconds() {
  return Date.now() / 1000;
}

function cachedChat(msg) {
  return msg?.chat || msg?._chat || null;
}

function cachedSender(msg) {
  return msg?.sender || msg?._sender || null;
}

export function cachedPrivatePeer(msg) {
  if (!msg?.isPrivate) return null;
  return cachedChat(msg) || (msg.out ? null : cachedSender(msg)) || null;
}

export function shouldForwardHotInboundMessage({ msg, meId = null }) {
  if (!msg?.isPrivate) return false;

  const chat = cachedChat(msg);
  const sender = cachedSender(msg);
  const peer = chat || (msg.out ? null : sender);
  if (peer) {
    return isPrivateHumanEntity(peer, meId);
  }

  const chatId = msg.chatId || msg.peerId;
  if (meId != null && chatId != null && String(chatId) === String(meId)) {
    return false;
  }
  return true;
}

export function buildHotInboundEvent({
  runtime,
  msg,
  resolvedPeer = null,
  isHistorical = false,
  telegramUpdateReceivedAt = nowSeconds(),
  telegramStateAppliedAt = null,
  hotEventBuiltAt = nowSeconds(),
  connectorTelemetry = {},
} = {}) {
  const me = runtime?.latestMe || null;
  if (!shouldForwardHotInboundMessage({ msg, meId: me?.id ?? null })) {
    return null;
  }

  // Cold gramjs cache (e.g. right after a sidecar restart) leaves msg.chat/
  // msg.sender empty; the caller's unknown-peer resolve already fetched the
  // entity for the human filter — reuse it so name/username are never lost.
  const peerFallback = msg?.isPrivate ? resolvedPeer : null;
  const chat = cachedChat(msg) || peerFallback;
  const sender = cachedSender(msg) || (msg.out ? null : peerFallback);
  const chatId = msg.chatId || msg.peerId;
  const senderId = msg.senderId || msg.fromId || chatId;
  if (!chatId || !senderId || !msg?.id) {
    return null;
  }

  return buildInboundEvent({
    workspaceId: runtime?.workspaceId,
    sellerUserId: me?.id,
    chatId,
    senderId,
    senderName: displayNameForMessage({
      chat,
      sender,
      isOutgoing: msg.out || false,
    }) || '',
    // inbound sender's @username (private-chat peer = the customer) — owner
    // cards build a t.me jump link from it; null for outgoing / no username
    senderUsername: (msg.out ? null : (sender?.username || chat?.username)) || null,
    msg,
    isHistorical,
    telemetry: {
      telegram_update_received_at: telegramUpdateReceivedAt,
      telegram_state_applied_at: telegramStateAppliedAt,
      hot_event_built_at: hotEventBuiltAt,
      ...connectorTelemetry,
    },
  });
}
