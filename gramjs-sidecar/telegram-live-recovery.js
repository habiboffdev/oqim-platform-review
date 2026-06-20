export const DEFAULT_LIVE_RECOVERY_MAX_AGE_SECONDS = 10 * 60;

function messageAgeSeconds(message, nowSeconds) {
  const sentAt = Number(message?.date || 0);
  if (!Number.isFinite(sentAt) || sentAt <= 0) return null;
  return Math.max(0, Math.floor(nowSeconds - sentAt));
}

export function shouldPromoteCatchUpMessageToLiveRecovery(
  runtime,
  message,
  {
    nowSeconds = Date.now() / 1000,
    maxAgeSeconds = DEFAULT_LIVE_RECOVERY_MAX_AGE_SECONDS,
  } = {},
) {
  if (!runtime?.workspaceId || runtime.connectionState !== 'connected') return false;
  if (!message?.id || message.out) return false;

  const ageSeconds = messageAgeSeconds(message, nowSeconds);
  if (ageSeconds == null) return false;
  return ageSeconds <= maxAgeSeconds;
}
