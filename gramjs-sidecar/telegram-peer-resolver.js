import bigInt from 'big-integer';
import { Api } from 'telegram';

function asString(value) {
  if (value == null || value === '') return null;
  return String(value);
}

function asLong(value) {
  const stringValue = asString(value);
  return stringValue ? bigInt(stringValue) : null;
}

function normalizeChatId(value) {
  const stringValue = asString(value);
  if (!stringValue) return null;
  return /^-?\d+$/.test(stringValue) ? Number(stringValue) : stringValue;
}

export function buildInputPeerFromRef(ref) {
  if (!ref?.className) return null;
  if (ref.className === 'InputPeerUser' && ref.userId && ref.accessHash) {
    return new Api.InputPeerUser({
      userId: asLong(ref.userId),
      accessHash: asLong(ref.accessHash),
    });
  }
  if (ref.className === 'InputPeerChannel' && ref.channelId && ref.accessHash) {
    return new Api.InputPeerChannel({
      channelId: asLong(ref.channelId),
      accessHash: asLong(ref.accessHash),
    });
  }
  if (ref.className === 'InputPeerChat' && ref.chatId) {
    return new Api.InputPeerChat({
      chatId: asLong(ref.chatId),
    });
  }
  return null;
}

function inputPeerRefFromRuntimeState(runtime, chatId) {
  const normalized = asString(chatId);
  if (!normalized) return null;
  const state = runtime?.telegramState;
  const user = state?.users?.get(normalized);
  if (user?.accessHash) {
    return {
      className: 'InputPeerUser',
      userId: normalized,
      accessHash: user.accessHash,
      source: 'runtime_state',
    };
  }
  const chat = state?.chats?.get(normalized);
  if (!chat) return null;
  if ((chat.broadcast || chat.megagroup) && chat.accessHash) {
    return {
      className: 'InputPeerChannel',
      channelId: normalized,
      accessHash: chat.accessHash,
      source: 'runtime_state',
    };
  }
  return {
    className: 'InputPeerChat',
    chatId: normalized,
    source: 'runtime_state',
  };
}

export function createTelegramPeerResolver({
  durableStateStore = null,
  withRpcTimeout = (promise) => promise,
} = {}) {
  async function resolve(runtime, chatId, { workspaceId = null, purpose = 'command' } = {}) {
    const resolvedWorkspaceId = workspaceId || runtime?.workspaceId;
    const statePeer = buildInputPeerFromRef(inputPeerRefFromRuntimeState(runtime, chatId));
    if (statePeer) return statePeer;

    const durableRef = durableStateStore?.findInputPeerRef
      ? await durableStateStore.findInputPeerRef(resolvedWorkspaceId, chatId)
      : null;
    const durablePeer = buildInputPeerFromRef(durableRef);
    if (durablePeer) return durablePeer;

    if (runtime?.client?.getInputEntity) {
      return withRpcTimeout(
        runtime.client.getInputEntity(normalizeChatId(chatId)),
        `RESOLVE_PEER_${purpose}_${resolvedWorkspaceId || 'unknown'}`,
      );
    }
    return chatId;
  }

  return { resolve };
}
