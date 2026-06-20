import { Api } from 'telegram';

import { json, parseBody, requireWorkspaceId } from '../http-utils.js';

export function createTypingRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  resolvePeer = async (_runtime, chatId) => chatId,
}) {
  return async function handleTypingRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;
    const { chatId } = body;
    if (!chatId) {
      json(res, 400, { error: 'chatId required' });
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (runtime.connectionState !== 'connected' || !runtime.client) {
      json(res, 503, { error: 'Not connected to Telegram' });
      return true;
    }

    try {
      const typing = body.typing !== false;
      const peer = await resolvePeer(runtime, chatId, { workspaceId, purpose: 'typing' });
      await withRpcTimeout(
        runtime.client.invoke(
          new Api.messages.SetTyping({
            peer,
            action: typing
              ? new Api.SendMessageTypingAction()
              : new Api.SendMessageCancelAction(),
          }),
        ),
        `SET_TYPING_${workspaceId}`,
      );
      json(res, 200, { ok: true, typing });
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.warn(`[Typing] ${runtimeLabel(runtime)} failed:`, err.message);
      json(res, 200, { ok: false, warning: err.message });
      return true;
    }
  };
}

export function createReadRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  readReceiptsEnabled = true,
  resolvePeer = async (_runtime, chatId) => chatId,
}) {
  return async function handleReadRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;
    const { chatId, maxId } = body;
    if (!chatId) {
      json(res, 400, { error: 'chatId required' });
      return true;
    }
    if (!readReceiptsEnabled && body.allowReadReceipt !== true) {
      json(res, 200, { ok: false, skipped: true, warning: 'read_receipts_disabled' });
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (runtime.connectionState !== 'connected' || !runtime.client) {
      json(res, 503, { error: 'Not connected to Telegram' });
      return true;
    }

    try {
      const peer = await resolvePeer(runtime, chatId, { workspaceId, purpose: 'read' });
      await withRpcTimeout(
        runtime.client.markAsRead(peer, maxId),
        `MARK_READ_${workspaceId}`,
      );
      json(res, 200, { ok: true });
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.warn(`[Read] ${runtimeLabel(runtime)} failed:`, err.message);
      json(res, 200, { ok: false, warning: err.message });
      return true;
    }
  };
}

export function createOnlineRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  onlinePresenceEnabled = true,
}) {
  return async function handleOnlineRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;
    if (!onlinePresenceEnabled && body.allowOnlinePresence !== true) {
      json(res, 200, { ok: false, skipped: true, warning: 'online_presence_disabled' });
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (runtime.connectionState !== 'connected' || !runtime.client) {
      json(res, 503, { error: 'Not connected to Telegram' });
      return true;
    }

    try {
      await withRpcTimeout(
        runtime.client.invoke(
          new Api.account.UpdateStatus({
            offline: false,
          }),
        ),
        `SET_ONLINE_${workspaceId}`,
      );
      json(res, 200, { ok: true });
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.warn(`[Online] ${runtimeLabel(runtime)} failed:`, err.message);
      json(res, 200, { ok: false, warning: err.message });
      return true;
    }
  };
}
