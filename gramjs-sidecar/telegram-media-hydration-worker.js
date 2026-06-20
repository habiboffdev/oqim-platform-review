import { TELEGRAM_QUEUE_PAUSED } from './telegram-method-queue.js';

const DEFAULT_MEDIA_TYPE = 'application/octet-stream';
const DEFAULT_FAILED_RETRY_AFTER_SECONDS = 300;
const DEFAULT_MAX_FAILED_ATTEMPTS = 2;

function isConnectedRuntime(runtime) {
  return runtime?.workspaceId && runtime.connectionState === 'connected' && runtime.client;
}

function mediaTypeFor(ref, message) {
  return (
    ref?.mimeType
    || message?.document?.mimeType
    || message?.media?.document?.mimeType
    || DEFAULT_MEDIA_TYPE
  );
}

export async function hydratePendingMediaRefs({
  runtime,
  durableStateStore,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
  withIsolatedMediaClient,
  withRpcTimeout = async (promise) => promise,
  onHydratedMediaRef,
  resolvePeer = async (_runtime, chatId) => chatId,
  limit = 10,
  failedRetryAfterSeconds = DEFAULT_FAILED_RETRY_AFTER_SECONDS,
  maxFailedAttempts = DEFAULT_MAX_FAILED_ATTEMPTS,
  nowSeconds = Date.now() / 1000,
} = {}) {
  if (!isConnectedRuntime(runtime) || !durableStateStore?.listPendingMediaRefs) {
    return { scanned: 0, hydrated: 0, failed: 0, paused: 0 };
  }
  if (!withIsolatedMediaClient) {
    throw new Error('withIsolatedMediaClient is required');
  }

  const allRefs = await durableStateStore.listPendingMediaRefs(runtime.workspaceId, { limit });
  const refs = allRefs.filter((ref) => shouldRetryMediaRef(ref, {
    failedRetryAfterSeconds,
    maxFailedAttempts,
    nowSeconds,
  }));
  let hydrated = 0;
  let failed = 0;
  let paused = 0;

  for (const ref of refs) {
    try {
      await runQueuedTelegramMethod(
        runtime,
        {
          methodClass: 'media_fetch',
          label: `HYDRATE_MEDIA_${runtime.workspaceId}_${ref.mediaKey}`,
          jobKind: 'media_hydration',
          jobKey: ref.mediaKey,
          priority: 4,
          cursor: {
            chatId: ref.chatId,
            messageId: ref.messageId,
            mediaKey: ref.mediaKey,
          },
        },
        async () => {
          await withIsolatedMediaClient(runtime, async (mediaClient) => {
            const inputPeer = await resolvePeer(
              { ...runtime, client: mediaClient },
              ref.chatId,
              { workspaceId: runtime.workspaceId, purpose: 'media_hydration' },
            );
            const messages = await withRpcTimeout(
              mediaClient.getMessages(inputPeer, { ids: [Number(ref.messageId)] }),
              `GET_MEDIA_MESSAGE_${runtime.workspaceId}`,
            );
            const message = messages?.[0];
            if (!message?.media) {
              throw new Error('MEDIA_REF_NOT_FOUND');
            }
            const content = await withRpcTimeout(
              mediaClient.downloadMedia(message, undefined),
              `DOWNLOAD_MEDIA_REF_${runtime.workspaceId}`,
            );
            if (!content?.length) {
              throw new Error('MEDIA_DOWNLOAD_EMPTY');
            }
            if (!onHydratedMediaRef) {
              throw new Error('MEDIA_HYDRATION_SINK_MISSING');
            }
            await onHydratedMediaRef(ref, {
              content,
              mediaType: mediaTypeFor(ref, message),
              downloadedAt: Date.now() / 1000,
            });
          });
        },
      );
      await durableStateStore.markMediaRefHydrated?.(ref);
      hydrated += 1;
    } catch (err) {
      if (err?.code === TELEGRAM_QUEUE_PAUSED) {
        paused += 1;
        break;
      }
      await durableStateStore.markMediaRefFailed?.(ref, err);
      failed += 1;
    }
  }

  return {
    scanned: refs.length,
    hydrated,
    failed,
    paused,
  };
}

function shouldRetryMediaRef(ref, {
  failedRetryAfterSeconds,
  maxFailedAttempts,
  nowSeconds,
}) {
  if (ref?.status !== 'failed') {
    return true;
  }
  if (Number(ref.attempts || 0) >= maxFailedAttempts) {
    return false;
  }
  const updatedAt = Number(ref.updatedAt || 0);
  if (!Number.isFinite(updatedAt) || updatedAt <= 0) {
    return false;
  }
  return nowSeconds - updatedAt >= failedRetryAfterSeconds;
}
