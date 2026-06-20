const TRANSIENT_DB_ERROR_CODES = new Set([
  '57P03',
  '08000',
  '08001',
  '08003',
  '08006',
  'ECONNREFUSED',
  'ECONNRESET',
  'ETIMEDOUT',
  'EHOSTUNREACH',
  'ENOTFOUND',
]);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function isTransientDbStartupError(err) {
  if (TRANSIENT_DB_ERROR_CODES.has(err?.code)) {
    return true;
  }
  const message = String(err?.message || '').toLowerCase();
  return message.includes('database system is starting up')
    || message.includes('the database system is starting up')
    || message.includes('connection refused')
    || message.includes('connect econnrefused');
}

export async function withStartupRetry(
  label,
  operation,
  {
    timeoutMs = 60_000,
    initialDelayMs = 250,
    maxDelayMs = 2_000,
    retryable = isTransientDbStartupError,
    sleepFn = sleep,
    nowFn = () => Date.now(),
    onRetry = null,
  } = {},
) {
  const deadline = nowFn() + timeoutMs;
  let delayMs = initialDelayMs;
  let attempt = 0;
  let lastError = null;

  while (nowFn() <= deadline) {
    attempt += 1;
    try {
      return await operation();
    } catch (err) {
      lastError = err;
      if (!retryable(err)) {
        throw err;
      }
      if (nowFn() + delayMs > deadline) {
        break;
      }
      onRetry?.({ attempt, delayMs, error: err });
      await sleepFn(delayMs);
      delayMs = Math.min(maxDelayMs, Math.ceil(delayMs * 1.7));
    }
  }

  const message = `${label} was not ready within ${timeoutMs}ms`;
  const timeoutError = new Error(message, { cause: lastError });
  timeoutError.code = 'STARTUP_RETRY_EXHAUSTED';
  throw timeoutError;
}
