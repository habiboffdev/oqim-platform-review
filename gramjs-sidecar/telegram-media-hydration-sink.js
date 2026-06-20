function encodeBase64(content) {
  if (Buffer.isBuffer(content)) {
    return content.toString('base64');
  }
  if (content instanceof Uint8Array) {
    return Buffer.from(content).toString('base64');
  }
  throw new Error('MEDIA_HYDRATION_CONTENT_INVALID');
}

export function buildMediaHydrationWebhookPayload(ref, payload = {}) {
  if (!ref?.workspaceId || !ref.chatId || !ref.messageId || !ref.mediaKey) {
    throw new Error('MEDIA_HYDRATION_REF_INVALID');
  }
  return {
    workspaceId: ref.workspaceId,
    chatId: String(ref.chatId),
    messageId: String(ref.messageId),
    mediaKey: ref.mediaKey,
    mediaKind: ref.mediaKind || null,
    documentId: ref.documentId || null,
    photoId: ref.photoId || null,
    mimeType: payload.mediaType || ref.mimeType || 'application/octet-stream',
    size: ref.size ?? null,
    contentBase64: encodeBase64(payload.content),
    downloadedAt: payload.downloadedAt || Date.now() / 1000,
    source: 'sidecar_media_hydration',
  };
}

export async function postHydratedMediaRefToBackend(
  { postJson } = {},
  ref,
  payload = {},
) {
  if (!postJson) {
    throw new Error('MEDIA_HYDRATION_BACKEND_POST_MISSING');
  }
  return postJson(
    '/api/webhook/telegram/media-hydration',
    buildMediaHydrationWebhookPayload(ref, payload),
  );
}
