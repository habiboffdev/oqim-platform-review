import { json, parseBody, requireWorkspaceId } from '../http-utils.js';

export function createEditRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  editIdempotencyCache,
  resolvePeer = async (_runtime, chatId) => chatId,
}) {
  return async function handleEditRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;

    const { chatId, messageId, text, idempotencyKey } = body;
    if (!chatId || !messageId || !text || !idempotencyKey) {
      json(res, 400, { error: 'chatId, messageId, text, and idempotencyKey required' });
      return true;
    }

    async function handleEditError(err, runtime = null) {
      await editIdempotencyCache.forget(workspaceId, idempotencyKey);
      if (err.seconds) {
        res.setHeader('Retry-After', String(err.seconds));
        json(res, 429, { error: 'Rate limited', retryAfter: err.seconds });
        return true;
      }
      if (err.message?.includes('MESSAGE_ID_INVALID')) {
        json(res, 404, { error: 'Message not found' });
        return true;
      }
      if (err.message?.includes('MESSAGE_EDIT_TIME_EXPIRED')) {
        json(res, 409, { error: 'Message edit window expired' });
        return true;
      }
      if (err.message?.includes('PEER_ID_INVALID') || err.message?.includes('USER_NOT_FOUND')) {
        json(res, 404, { error: 'Chat not found' });
        return true;
      }
      if (runtime) {
        await markRuntimeRpcFailure(runtime, err);
        console.error(`[Edit] ${runtimeLabel(runtime)} failed:`, err.message);
      }
      telegramApiError(res, err, 'Telegram edit failed');
      return true;
    }

    const cached = await editIdempotencyCache.get(workspaceId, idempotencyKey);
    if (cached?.response) {
      json(res, 200, { ...cached.response, idempotentReplay: true });
      return true;
    }
    if (cached?.promise) {
      try {
        const response = await cached.promise;
        json(res, 200, { ...response, idempotentReplay: true });
      } catch (err) {
        await handleEditError(err);
      }
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (runtime.connectionState !== 'connected' || !runtime.client) {
      json(res, 503, { error: 'Not connected to Telegram' });
      return true;
    }

    try {
      const peer = await resolvePeer(runtime, chatId, { workspaceId, purpose: 'edit' });
      const editPromise = withRpcTimeout(
        runtime.client.editMessage(peer, {
          message: Number(messageId),
          text,
        }),
        `EDIT_MESSAGE_${workspaceId}`,
      ).then((result) => ({
        externalMessageId: result?.id ?? Number(messageId),
        chatId: String(chatId),
        date: result?.date ?? null,
      }));
      await editIdempotencyCache.rememberPromise(workspaceId, idempotencyKey, editPromise);
      const response = await editPromise;
      await editIdempotencyCache.rememberResult(workspaceId, idempotencyKey, response);
      json(res, 200, response);
      return true;
    } catch (err) {
      return handleEditError(err, runtime);
    }
  };
}
