function asString(value) {
  if (value == null) return null;
  return String(value);
}

function compactObject(value) {
  return Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined),
  );
}

function isChatEntity(entity) {
  return Boolean(entity?.title || entity?.broadcast || entity?.megagroup);
}

export function createTelegramStateStore() {
  return {
    users: new Map(),
    chats: new Map(),
    messages: new Map(),
    updateState: {
      lastReceivedAt: null,
      lastAppliedAt: null,
    },
  };
}

export function ensureTelegramState(runtime) {
  if (!runtime.telegramState) {
    runtime.telegramState = createTelegramStateStore();
  }
  return runtime.telegramState;
}

export function rememberPeer(state, entity, receivedAt = null) {
  const id = asString(entity?.id);
  if (!id) return null;

  const record = compactObject({
    id,
    accessHash: asString(entity.accessHash),
    firstName: entity.firstName,
    lastName: entity.lastName,
    username: entity.username,
    phone: entity.phone,
    title: entity.title,
    bot: entity.bot === undefined ? undefined : Boolean(entity.bot),
    self: entity.self === undefined ? undefined : Boolean(entity.self),
    support: entity.support === undefined ? undefined : Boolean(entity.support),
    deleted: entity.deleted === undefined ? undefined : Boolean(entity.deleted),
    broadcast: entity.broadcast === undefined ? undefined : Boolean(entity.broadcast),
    megagroup: entity.megagroup === undefined ? undefined : Boolean(entity.megagroup),
    updatedAt: receivedAt,
  });

  if (isChatEntity(entity)) {
    state.chats.set(id, record);
    return { kind: 'chat', id, record };
  }
  state.users.set(id, record);
  return { kind: 'user', id, record };
}

export function applyHotMessageState(runtime, msg, {
  telegramUpdateReceivedAt = Date.now() / 1000,
  resolvedPeer = null,
} = {}) {
  const state = ensureTelegramState(runtime);
  state.updateState.lastReceivedAt = telegramUpdateReceivedAt;

  // Cold gramjs cache (first-contact customer, or right after a restart) leaves
  // msg.chat/msg.sender empty. The caller already resolved the entity for the
  // human filter — reuse it so the peer (with access_hash) is cached and the
  // first reply can resolve its InputPeer instead of throwing (#417).
  const peerFallback = msg?.isPrivate ? resolvedPeer : null;
  rememberPeer(state, msg?.chat || msg?._chat || peerFallback, telegramUpdateReceivedAt);
  rememberPeer(
    state,
    msg?.sender || msg?._sender || (msg?.out ? null : peerFallback),
    telegramUpdateReceivedAt,
  );

  const chatId = asString(msg?.chatId || msg?.peerId);
  const messageId = asString(msg?.id);
  if (chatId && messageId) {
    state.messages.set(`${chatId}:${messageId}`, compactObject({
      workspaceId: runtime?.workspaceId,
      chatId,
      messageId,
      senderId: asString(msg.senderId || msg.fromId || chatId),
      date: msg.date,
      text: msg.message || '',
      isOutgoing: Boolean(msg.out),
      mediaType: msg.media?.className || null,
      receivedAt: telegramUpdateReceivedAt,
    }));
  }

  const appliedAt = Date.now() / 1000;
  state.updateState.lastAppliedAt = appliedAt;
  return appliedAt;
}

export function telegramStateCounts(runtime) {
  const state = runtime?.telegramState;
  return {
    users: state?.users?.size || 0,
    chats: state?.chats?.size || 0,
    messages: state?.messages?.size || 0,
  };
}
