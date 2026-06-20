import { Api } from 'telegram';
import { CustomFile } from 'telegram/client/uploads.js';

import { json, parseBody, requireWorkspaceId } from '../http-utils.js';

const MAX_OUTBOUND_PHOTO_BYTES = 10 * 1024 * 1024;

export function createSendRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  sendIdempotencyCache,
  resolvePeer = async (_runtime, chatId) => chatId,
  fetchOutboundMedia = fetchOutboundPhotoMedia,
}) {
  return async function handleSendRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;
    const { chatId, text, caption, idempotencyKey } = body;
    const replyToMsgId = normalizeReplyToMsgId(body.replyToMsgId);
    const media = normalizeOutboundMedia(body.media);
    if (media.error) {
      json(res, 400, { error: media.error });
      return true;
    }
    if (replyToMsgId.error) {
      json(res, 400, { error: replyToMsgId.error });
      return true;
    }
    if (!chatId || (!text && !media.payload) || !idempotencyKey) {
      json(res, 400, { error: 'chatId, text or media.url, and idempotencyKey required' });
      return true;
    }

    async function handleSendError(err, runtime = null) {
      await sendIdempotencyCache.forget(workspaceId, idempotencyKey);
      if (err.seconds) {
        res.setHeader('Retry-After', String(err.seconds));
        json(res, 429, { error: 'Rate limited', retryAfter: err.seconds });
        return true;
      }
      if (err.message?.includes('PEER_ID_INVALID') || err.message?.includes('USER_NOT_FOUND')) {
        json(res, 404, { error: 'Chat not found' });
        return true;
      }
      if (runtime) {
        await markRuntimeRpcFailure(runtime, err);
        console.error(`[Send] ${runtimeLabel(runtime)} failed:`, err.message);
      }
      telegramApiError(res, err, 'Telegram send failed');
      return true;
    }

    const cached = await sendIdempotencyCache.get(workspaceId, idempotencyKey);
    if (cached?.response) {
      json(res, 200, { ...cached.response, idempotentReplay: true });
      return true;
    }
    if (cached?.promise) {
      try {
        const response = await cached.promise;
        json(res, 200, { ...response, idempotentReplay: true });
      } catch (err) {
        await handleSendError(err);
      }
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (runtime.connectionState !== 'connected' || !runtime.client) {
      json(res, 503, { error: 'Not connected to Telegram' });
      return true;
    }

    try {
      const peer = await resolvePeer(runtime, chatId, { workspaceId, purpose: 'send' });
      const replyToPayload = replyToMsgId.value ? { replyTo: replyToMsgId.value } : {};
      const mediaFile = media.payload
        ? (media.payload.document
          ? await toGramjsDocumentFile(runtime, media.payload, { withRpcTimeout, workspaceId, resolvePeer })
          : await toGramjsMediaFile(media.payload, { fetchOutboundMedia }))
        : null;
      let effectiveCaption = caption;
      if ((effectiveCaption == null || effectiveCaption === '') && mediaFile?.sourceCaption) {
        effectiveCaption = mediaFile.sourceCaption;
      }
      const sendPromise = withRpcTimeout(
        media.payload
          ? runtime.client.sendFile(peer, {
            file: mediaFile.file,
            caption: effectiveCaption ?? text ?? '',
            forceDocument: mediaFile.forceDocument,
            ...replyToPayload,
          })
          : runtime.client.sendMessage(peer, { message: text, ...replyToPayload }),
        `SEND_MESSAGE_${workspaceId}`,
      ).then((result) => ({
        externalMessageId: result.id,
        chatId: String(chatId),
        date: result.date,
        ...(media.payload ? { mediaType: media.payload.mediaType } : {}),
      }));
      await sendIdempotencyCache.rememberPromise(workspaceId, idempotencyKey, sendPromise);
      const response = await sendPromise;
      await sendIdempotencyCache.rememberResult(workspaceId, idempotencyKey, response);
      json(res, 200, response);
      return true;
    } catch (err) {
      if (err && err.permanent) {
        json(res, 422, { error: err.message || 'vault_document_unavailable' });
        return true;
      }
      return handleSendError(err, runtime);
    }
  };
}

function normalizeReplyToMsgId(value) {
  if (value === undefined || value === null || value === '') {
    return { value: null };
  }
  const numeric = Number(value);
  if (!Number.isSafeInteger(numeric) || numeric <= 0) {
    return { error: 'replyToMsgId must be a positive integer' };
  }
  return { value: numeric };
}

function normalizeOutboundMedia(media) {
  if (!media) {
    return { payload: null };
  }
  if (media.document && media.document.vaultMessageId != null && media.document.vaultPeer) {
    return {
      payload: {
        document: {
          vaultPeer: String(media.document.vaultPeer),
          vaultMessageId: Number(media.document.vaultMessageId),
        },
        mediaType: normalizeMediaType(media.mediaType),
        mimeType: typeof media.mimeType === 'string' ? media.mimeType.trim() : '',
        fileName: typeof media.fileName === 'string' ? media.fileName.trim() : '',
      },
    };
  }
  const url = typeof media.url === 'string' ? media.url.trim() : '';
  if (!url) {
    return { error: 'media.url required' };
  }
  if (!url.startsWith('https://') && !url.startsWith('http://')) {
    return { error: 'media.url must be an http(s) URL' };
  }
  return {
    payload: {
      url,
      mediaType: normalizeMediaType(media.mediaType),
      mimeType: typeof media.mimeType === 'string' ? media.mimeType.trim() : '',
      fileName: typeof media.fileName === 'string' ? media.fileName.trim() : '',
    },
  };
}

function normalizeMediaType(mediaType) {
  const normalized = typeof mediaType === 'string' ? mediaType.trim().toLowerCase() : '';
  if (['photo', 'video', 'document'].includes(normalized)) {
    return normalized;
  }
  return 'photo';
}

async function toGramjsDocumentFile(runtime, payload, { withRpcTimeout, workspaceId, resolvePeer }) {
  const vaultPeer = await resolvePeer(runtime, payload.document.vaultPeer, {
    workspaceId,
    purpose: 'vault_send',
  });
  const messages = await withRpcTimeout(
    runtime.client.getMessages(vaultPeer, { ids: [payload.document.vaultMessageId] }),
    `VAULT_GET_${workspaceId}`,
  );
  const doc = messages?.[0]?.media?.document;
  if (!doc) {
    throw Object.assign(new Error('vault_document_unavailable'), { permanent: true });
  }
  return {
    file: new Api.InputDocument({
      id: doc.id,
      accessHash: doc.accessHash,
      fileReference: doc.fileReference,
    }),
    forceDocument: false,
    // Live channel-post caption (read for free from the getMessages above). Used
    // as the default caption when the caller did not pass an explicit one, so the
    // channel post stays the single source of truth.
    sourceCaption: String(messages?.[0]?.message || ''),
  };
}

async function toGramjsMediaFile(media, { fetchOutboundMedia }) {
  if (media.mediaType !== 'photo') {
    return {
      file: media.url,
      forceDocument: media.mediaType === 'document',
    };
  }
  const downloaded = await fetchOutboundMedia(media);
  return {
    file: new CustomFile(
      downloaded.fileName,
      downloaded.buffer.length,
      '',
      downloaded.buffer,
    ),
    forceDocument: false,
  };
}

async function fetchOutboundPhotoMedia(media) {
  const response = await fetch(media.url, { redirect: 'follow' });
  if (!response.ok) {
    throw new Error(`Failed to download outbound photo: HTTP ${response.status}`);
  }
  const contentType = response.headers.get('content-type') || media.mimeType || '';
  if (contentType && !contentType.toLowerCase().startsWith('image/')) {
    throw new Error(`Outbound photo URL returned non-image content-type: ${contentType}`);
  }
  const contentLength = Number(response.headers.get('content-length') || 0);
  if (contentLength > MAX_OUTBOUND_PHOTO_BYTES) {
    throw new Error('Outbound photo exceeds Telegram compressed-photo size guard');
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  if (buffer.length > MAX_OUTBOUND_PHOTO_BYTES) {
    throw new Error('Outbound photo exceeds Telegram compressed-photo size guard');
  }
  return {
    buffer,
    contentType,
    fileName: normalizePhotoFileName(media, contentType),
  };
}

function normalizePhotoFileName(media, contentType) {
  const raw = media.fileName || fileNameFromUrl(media.url) || '';
  const safe = raw.replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '');
  if (/\.(png|jpe?g)$/i.test(safe)) {
    return safe;
  }
  const extension = imageExtension(contentType) || imageExtension(media.mimeType) || '.jpg';
  const base = safe.replace(/\.[^.]+$/, '') || 'photo';
  return `${base}${extension}`;
}

function fileNameFromUrl(url) {
  try {
    const parsed = new URL(url);
    const last = parsed.pathname.split('/').filter(Boolean).pop();
    return last ? decodeURIComponent(last) : '';
  } catch {
    return '';
  }
}

function imageExtension(contentType) {
  const normalized = String(contentType || '').toLowerCase().split(';')[0].trim();
  if (normalized === 'image/png') return '.png';
  if (normalized === 'image/jpeg' || normalized === 'image/jpg') return '.jpg';
  return '';
}
