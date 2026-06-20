import { Api } from 'telegram';

import { json, parseBody, requireWorkspaceId } from '../http-utils.js';

function ensureConnected(res, runtime) {
  if (runtime.connectionState !== 'connected' || !runtime.client) {
    json(res, 503, { error: 'Not connected to Telegram' });
    return false;
  }
  return true;
}

export function createDownloadMediaRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  withIsolatedMediaClient,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  responseCommitted,
  isClientAbortError,
  listThumbCandidates,
  createResponseWriter,
  fallbackDownloadMime,
  streamMediaRange,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
  resolvePeer = async (_runtime, chatId) => chatId,
}) {
  return async function handleDownloadMediaRoute(req, res, url) {
    const body = await parseBody(req);
    const workspaceId = requireWorkspaceId(res, body, url);
    if (!workspaceId) return true;
    const { chatId, messageId } = body;
    const thumb = body.thumb === true;
    const byteRange = typeof body.byteRange === 'string' ? body.byteRange : null;
    if (!chatId || !messageId) {
      json(res, 400, { error: 'chatId and messageId required' });
      return true;
    }
    if (thumb && byteRange) {
      json(res, 400, { error: 'byteRange is only supported for full media' });
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (!ensureConnected(res, runtime)) return true;

    try {
      await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'media_fetch',
          label: `DOWNLOAD_MEDIA_${workspaceId}`,
          priority: 4,
        },
        () => withIsolatedMediaClient(runtime, async (mediaClient) => {
          const inputPeer = await resolvePeer(
            { ...runtime, client: mediaClient },
            chatId,
            { workspaceId, purpose: 'media_download' },
          );
          const messages = await withRpcTimeout(
            mediaClient.getMessages(inputPeer, {
              ids: [parseInt(messageId, 10)],
            }),
            `GET_MEDIA_MESSAGE_${workspaceId}`,
          );
          if (!messages.length || !messages[0]?.media) {
            json(res, 404, { error: thumb ? 'Thumbnail not available' : 'Media download failed' });
            return;
          }

          if (thumb) {
            const thumbCandidates = listThumbCandidates(messages[0].document || messages[0].photo);
            if (!thumbCandidates.length) {
              json(res, 404, { error: 'Thumbnail not available' });
              return;
            }

            for (const candidate of thumbCandidates) {
              const writer = createResponseWriter(
                res,
                fallbackDownloadMime(messages[0], true),
              );
              try {
                await mediaClient.downloadMedia(messages[0], {
                  thumb: candidate,
                  outputFile: writer,
                });
              } finally {
                writer.close();
              }
              if (writer.bytesWritten > 0) {
                return;
              }
            }

            json(res, 404, { error: 'Thumbnail not available' });
            return;
          }

          if (byteRange) {
            try {
              const streamed = await streamMediaRange(mediaClient, messages[0], byteRange, res);
              if (streamed) {
                return;
              }
            } catch (rangeErr) {
              if (rangeErr.message === 'INVALID_RANGE') {
                json(res, 416, { error: 'Invalid byte range' });
                return;
              }
              throw rangeErr;
            }
            json(res, 404, { error: 'Media download failed' });
            return;
          }

          const writer = createResponseWriter(
            res,
            fallbackDownloadMime(messages[0], false),
          );
          try {
            await mediaClient.downloadMedia(messages[0], {
              outputFile: writer,
            });
          } finally {
            writer.close();
          }
          if (writer.bytesWritten > 0) {
            return;
          }
          json(res, 404, { error: 'Media download failed' });
        }),
      );
      return true;
    } catch (err) {
      if (isClientAbortError(err) || responseCommitted(res)) {
        console.warn(`[Media] ${runtimeLabel(runtime)} stream ended early: ${err.message}`);
        return true;
      }
      await markRuntimeRpcFailure(runtime, err);
      console.error(`[Media] ${runtimeLabel(runtime)} failed:`, err.message);
      telegramApiError(res, err, 'Failed to download media');
      return true;
    }
  };
}

export function createCustomEmojiRouteHandler({
  ensureAuthorizedRuntime,
  withRpcTimeout,
  withIsolatedMediaClient,
  markRuntimeRpcFailure,
  runtimeLabel,
  telegramApiError,
  listThumbCandidates,
  createResponseWriter,
  sniffMediaMime,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
}) {
  return async function handleCustomEmojiRoute(_req, res, url) {
    const workspaceId = requireWorkspaceId(res, null, url);
    if (!workspaceId) return true;

    const documentIdRaw = url.searchParams.get('documentId');
    if (!documentIdRaw) {
      json(res, 400, { error: 'documentId is required' });
      return true;
    }

    let documentId;
    try {
      documentId = BigInt(documentIdRaw);
    } catch {
      json(res, 400, { error: 'documentId must be an integer string' });
      return true;
    }

    const runtime = await ensureAuthorizedRuntime(workspaceId);
    if (!ensureConnected(res, runtime)) return true;

    try {
      await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'media_fetch',
          label: `GET_CUSTOM_EMOJI_${workspaceId}`,
          priority: 4,
        },
        () => withIsolatedMediaClient(runtime, async (mediaClient) => {
          const documents = await withRpcTimeout(
            mediaClient.invoke(
              new Api.messages.GetCustomEmojiDocuments({
                documentId: [documentId],
              }),
            ),
            `GET_CUSTOM_EMOJI_${workspaceId}`,
          );
          const document = documents?.[0];
          if (!document) {
            json(res, 404, { error: 'Custom emoji not found' });
            return;
          }

          const thumbCandidates = listThumbCandidates(document);
          for (const candidate of thumbCandidates) {
            const writer = createResponseWriter(
              res,
              document.mimeType === 'application/x-tgsticker'
                ? 'image/webp'
                : (document.mimeType || 'application/octet-stream'),
            );
            try {
              await mediaClient.downloadMedia(document, {
                thumb: candidate,
                outputFile: writer,
              });
            } finally {
              writer.close();
            }
            if (writer.bytesWritten > 0) {
              return;
            }
          }

          const buffer = await mediaClient.downloadMedia(document, undefined);
          if (!buffer) {
            json(res, 404, { error: 'Custom emoji not found' });
            return;
          }

          const mime = (
            document.mimeType === 'application/x-tgsticker'
              ? 'image/webp'
              : (document.mimeType || sniffMediaMime(buffer, 'application/octet-stream'))
          );
          res.writeHead(200, {
            'Content-Type': mime,
            'Content-Length': buffer.length,
            'Cache-Control': 'private, max-age=86400',
          });
          res.end(buffer);
        }),
      );
      return true;
    } catch (err) {
      await markRuntimeRpcFailure(runtime, err);
      console.error(`[CustomEmoji] ${runtimeLabel(runtime)} failed:`, err.message);
      telegramApiError(res, err, 'Failed to fetch custom emoji');
      return true;
    }
  };
}
