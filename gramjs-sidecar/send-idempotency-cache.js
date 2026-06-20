export function createSendIdempotencyCache({ ttlMs = 5 * 60 * 1000, now = () => Date.now() } = {}) {
  const cache = new Map();

  function key(workspaceId, idempotencyKey) {
    return `${workspaceId}:${idempotencyKey}`;
  }

  function rememberResult(workspaceId, idempotencyKey, response) {
    if (!idempotencyKey) return;
    cache.set(key(workspaceId, idempotencyKey), {
      response,
      expiresAt: now() + ttlMs,
    });
  }

  function rememberPromise(workspaceId, idempotencyKey, promise) {
    if (!idempotencyKey) return;
    cache.set(key(workspaceId, idempotencyKey), {
      promise,
      expiresAt: now() + ttlMs,
    });
  }

  function forget(workspaceId, idempotencyKey) {
    if (!idempotencyKey) return;
    cache.delete(key(workspaceId, idempotencyKey));
  }

  function get(workspaceId, idempotencyKey) {
    if (!idempotencyKey) return null;
    const cacheKey = key(workspaceId, idempotencyKey);
    const cached = cache.get(cacheKey);
    if (!cached) return null;
    if (cached.expiresAt <= now()) {
      cache.delete(cacheKey);
      return null;
    }
    return cached;
  }

  return {
    rememberResult,
    rememberPromise,
    forget,
    get,
  };
}
