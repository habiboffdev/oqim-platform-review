import { json, requireWorkspaceId } from '../http-utils.js';

function ensureConnected(res, runtime) {
  if (runtime.connectionState !== 'connected' || !runtime.client) {
    json(res, 503, { error: 'Not connected to Telegram' });
    return false;
  }
  return true;
}

function liveClient(runtime, fn) {
  return fn(runtime.client);
}

export function createMessagesRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  serializeBackfillMessage,
  withBackgroundClient = liveClient,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
  resolvePeer = async (_runtime, chatId) => chatId,
}) {
  return async function handleMessagesRoute(_req, res, url) {
    const workspaceId = requireWorkspaceId(res, null, url);
    if (!workspaceId) return true;

    const chatId = url.searchParams.get('chatId');
    if (!chatId) {
      json(res, 400, { error: 'chatId is required' });
      return true;
    }

    const limit = Math.max(
      1,
      Math.min(parseInt(url.searchParams.get('limit') || '100', 10) || 100, 500),
    );
    const afterId = Math.max(0, parseInt(url.searchParams.get('afterId') || '0', 10) || 0);
    const beforeId = Math.max(0, parseInt(url.searchParams.get('beforeId') || '0', 10) || 0);

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (!ensureConnected(res, runtime)) return true;

    try {
      const options = afterId > 0
        ? { minId: afterId, limit }
        : beforeId > 0
          ? { maxId: beforeId - 1, limit }
          : { limit };
      const messages = await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'history_sync',
          label: `GET_MESSAGES_${workspaceId}`,
          priority: 4,
        },
        () => withBackgroundClient(
          runtime,
          (telegramClient) => withRpcTimeout(
            Promise.resolve(resolvePeer(
              { ...runtime, client: telegramClient },
              chatId,
              { workspaceId, purpose: 'messages' },
            )).then((peer) => telegramClient.getMessages(peer, options)),
            `GET_MESSAGES_${workspaceId}`,
          ),
        ),
      );
      const serialized = messages
        .filter((msg) => {
          if (!msg?.id) return false;
          if (afterId && msg.id <= afterId) return false;
          if (beforeId && msg.id >= beforeId) return false;
          return true;
        })
        .map(serializeBackfillMessage)
        .sort((a, b) => a.messageId - b.messageId);
      json(res, 200, serialized);
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.error(`[Messages] ${runtimeLabel(runtime)} failed:`, err.message);
      telegramApiError(res, err, 'Failed to fetch messages');
      return true;
    }
  };
}

export function createDialogsRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  ensureMe,
  serializeCurrentPrivateDialogs,
  syncDialogShells,
  withBackgroundClient = liveClient,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
}) {
  return async function handleDialogsRoute(_req, res, url) {
    const workspaceId = requireWorkspaceId(res, null, url);
    if (!workspaceId) return true;

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (!ensureConnected(res, runtime)) return true;

    try {
      const dialogs = await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'dialog_sync',
          label: `GET_DIALOGS_${workspaceId}`,
          priority: 3,
        },
        () => withBackgroundClient(
          runtime,
          (telegramClient) => withRpcTimeout(
            telegramClient.getDialogs({}),
            `GET_DIALOGS_${workspaceId}`,
          ),
        ),
      );
      const me = await ensureMe(runtime);
      const privateDialogs = serializeCurrentPrivateDialogs(dialogs, me?.id ?? null);
      if (privateDialogs.length) {
        await syncDialogShells(runtime, privateDialogs, dialogs);
      }
      json(res, 200, privateDialogs);
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.error(`[Dialogs] ${runtimeLabel(runtime)} failed:`, err.message);
      telegramApiError(res, err, 'Failed to fetch dialogs');
      return true;
    }
  };
}

export function createChannelsRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  serializeChannelDialog,
  withBackgroundClient = liveClient,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
}) {
  return async function handleChannelsRoute(_req, res, url) {
    const workspaceId = requireWorkspaceId(res, null, url);
    if (!workspaceId) return true;

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (!ensureConnected(res, runtime)) return true;

    try {
      const dialogs = await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'dialog_sync',
          label: `GET_CHANNELS_${workspaceId}`,
          priority: 3,
        },
        () => withBackgroundClient(
          runtime,
          (telegramClient) => withRpcTimeout(
            telegramClient.getDialogs({}),
            `GET_CHANNELS_${workspaceId}`,
          ),
        ),
      );
      const channels = dialogs
        .map(serializeChannelDialog)
        .filter(Boolean)
        .sort((a, b) => {
          if (a.is_own && !b.is_own) return -1;
          if (!a.is_own && b.is_own) return 1;
          return a.name.localeCompare(b.name);
        });
      json(res, 200, channels);
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.error(`[Channels] ${runtimeLabel(runtime)} failed:`, err.message);
      telegramApiError(res, err, 'Failed to fetch channels');
      return true;
    }
  };
}

export function createChannelPostsRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  serializeChannelPost,
  withBackgroundClient = liveClient,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
}) {
  return async function handleChannelPostsRoute(_req, res, url) {
    const workspaceId = requireWorkspaceId(res, null, url);
    if (!workspaceId) return true;

    const channelId = url.searchParams.get('channelId');
    if (!channelId) {
      json(res, 400, { error: 'channelId is required' });
      return true;
    }

    const limit = Math.max(
      1,
      Math.min(parseInt(url.searchParams.get('limit') || '100', 10) || 100, 300),
    );
    const dateFrom = parseDateBoundary(url.searchParams.get('dateFrom'), 'start');
    const dateTo = parseDateBoundary(url.searchParams.get('dateTo'), 'end');

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (!ensureConnected(res, runtime)) return true;

    try {
      const messages = await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'onboarding_import',
          label: `GET_CHANNEL_POSTS_${workspaceId}`,
          priority: 4,
        },
        () => withBackgroundClient(runtime, async (telegramClient) => {
          const trimmedChannelId = channelId.trim();
          const entityRef = /^-?\d+$/.test(trimmedChannelId)
            ? Number(trimmedChannelId)
            : trimmedChannelId.replace(/^@/, '');
          const entity = await withRpcTimeout(
            telegramClient.getEntity(entityRef),
            `GET_CHANNEL_ENTITY_${workspaceId}`,
          );
          return withRpcTimeout(
            telegramClient.getMessages(entity, { limit }),
            `GET_CHANNEL_POSTS_${workspaceId}`,
          );
        }),
      );
      const posts = messages
        .filter((msg) => msg?.id && !msg?.action)
        .map(serializeChannelPost)
        .filter((post) => isPostInsideDateRange(post, dateFrom, dateTo))
        .sort((a, b) => a.postId - b.postId);
      json(res, 200, posts);
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.error(`[ChannelPosts] ${runtimeLabel(runtime)} failed:`, err.message);
      telegramApiError(res, err, 'Failed to fetch channel posts');
      return true;
    }
  };
}

function parseDateBoundary(value, edge) {
  const text = String(value || '').trim();
  if (!text) return null;
  const isoDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(text);
  const source = isoDateOnly
    ? `${text}T${edge === 'end' ? '23:59:59.999' : '00:00:00.000'}Z`
    : text;
  const timestamp = Date.parse(source);
  if (!Number.isFinite(timestamp)) return null;
  return Math.floor(timestamp / 1000);
}

function isPostInsideDateRange(post, dateFrom, dateTo) {
  const rawDate = Number(post?.date || 0);
  if (!Number.isFinite(rawDate) || rawDate <= 0) return true;
  const postDate = rawDate > 1_000_000_000_000 ? Math.floor(rawDate / 1000) : rawDate;
  if (dateFrom !== null && postDate < dateFrom) return false;
  if (dateTo !== null && postDate > dateTo) return false;
  return true;
}
