import { Api } from 'telegram';

import { json, parseBody, requireWorkspaceId } from '../http-utils.js';

export function createReactRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  reactIdempotencyCache,
  resolvePeer = async (_runtime, chatId) => chatId,
}) {
  return async function handleReactRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;

    const { chatId, messageId, reaction, idempotencyKey } = body;
    const normalizedMessageId = normalizeMessageId(messageId);
    const normalizedReaction = normalizeReaction(reaction);
    if (!chatId || !idempotencyKey || normalizedMessageId.error || normalizedReaction.error) {
      json(res, 400, {
        error: normalizedMessageId.error
          || normalizedReaction.error
          || 'chatId, messageId, reaction, and idempotencyKey required',
      });
      return true;
    }

    async function handleReactError(err, runtime = null) {
      await reactIdempotencyCache.forget(workspaceId, idempotencyKey);
      if (err.seconds) {
        res.setHeader('Retry-After', String(err.seconds));
        json(res, 429, { error: 'Rate limited', retryAfter: err.seconds });
        return true;
      }
      if (err.message?.includes('MESSAGE_ID_INVALID')) {
        json(res, 404, { error: 'Message not found' });
        return true;
      }
      if (err.message?.includes('PEER_ID_INVALID') || err.message?.includes('USER_NOT_FOUND')) {
        json(res, 404, { error: 'Chat not found' });
        return true;
      }
      if (runtime) {
        await markRuntimeRpcFailure(runtime, err);
        console.error(`[React] ${runtimeLabel(runtime)} failed:`, err.message);
      }
      telegramApiError(res, err, 'Telegram reaction failed');
      return true;
    }

    const cached = await reactIdempotencyCache.get(workspaceId, idempotencyKey);
    if (cached?.response) {
      json(res, 200, { ...cached.response, idempotentReplay: true });
      return true;
    }
    if (cached?.promise) {
      try {
        const response = await cached.promise;
        json(res, 200, { ...response, idempotentReplay: true });
      } catch (err) {
        await handleReactError(err);
      }
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (runtime.connectionState !== 'connected' || !runtime.client) {
      json(res, 503, { error: 'Not connected to Telegram' });
      return true;
    }

    try {
      const peer = await resolvePeer(runtime, chatId, { workspaceId, purpose: 'react' });
      const reactPromise = withRpcTimeout(
        runtime.client.invoke(
          new Api.messages.SendReaction({
            peer,
            msgId: normalizedMessageId.value,
            reaction: [
              new Api.ReactionEmoji({
                emoticon: normalizedReaction.value,
              }),
            ],
          }),
        ),
        `SEND_REACTION_${workspaceId}`,
      ).then(() => ({
        externalMessageId: normalizedMessageId.value,
        chatId: String(chatId),
        reaction: normalizedReaction.value,
      }));
      await reactIdempotencyCache.rememberPromise(workspaceId, idempotencyKey, reactPromise);
      const response = await reactPromise;
      await reactIdempotencyCache.rememberResult(workspaceId, idempotencyKey, response);
      json(res, 200, response);
      return true;
    } catch (err) {
      return handleReactError(err, runtime);
    }
  };
}

function normalizeMessageId(value) {
  const numeric = Number(value);
  if (!Number.isSafeInteger(numeric) || numeric <= 0) {
    return { error: 'messageId must be a positive integer' };
  }
  return { value: numeric };
}

function normalizeReaction(value) {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) {
    return { error: 'reaction required' };
  }
  return { value: text };
}
