import { parseFloodWaitSeconds } from './telegram-errors.js';

export const TELEGRAM_QUEUE_PAUSED = 'TELEGRAM_QUEUE_PAUSED';

function ensureQueueState(runtime) {
  if (!runtime.telegramMethodQueues) {
    runtime.telegramMethodQueues = {
      tails: new Map(),
      floodWaits: new Map(),
    };
  }
  return runtime.telegramMethodQueues;
}

function queuePausedError(methodClass, pausedUntilMs) {
  const seconds = Math.max(1, Math.ceil((pausedUntilMs - Date.now()) / 1000));
  const err = new Error(`${TELEGRAM_QUEUE_PAUSED}:${methodClass}:${seconds}`);
  err.code = TELEGRAM_QUEUE_PAUSED;
  err.methodClass = methodClass;
  err.retryAfter = seconds;
  return err;
}

export async function runQueuedTelegramMethod(runtime, {
  methodClass,
  label,
  priority = 3,
}, fn) {
  const state = ensureQueueState(runtime);
  const floodWait = state.floodWaits.get(methodClass);
  if (floodWait && floodWait.pausedUntilMs > Date.now()) {
    throw queuePausedError(methodClass, floodWait.pausedUntilMs);
  }

  const previous = state.tails.get(methodClass) || Promise.resolve();
  const run = previous.catch(() => undefined).then(async () => {
    try {
      const result = await fn();
      state.floodWaits.delete(methodClass);
      return result;
    } catch (err) {
      const retryAfter = parseFloodWaitSeconds(err);
      if (retryAfter) {
        state.floodWaits.set(methodClass, {
          methodClass,
          priority,
          label,
          retryAfter,
          pausedUntilMs: Date.now() + retryAfter * 1000,
          lastError: err.message || String(err),
        });
      }
      throw err;
    }
  });
  const tail = run.catch(() => undefined).finally(() => {
    if (state.tails.get(methodClass) === tail) {
      state.tails.delete(methodClass);
    }
  });
  state.tails.set(methodClass, tail);
  return run;
}

export function telegramMethodQueueStatus(runtime) {
  const state = runtime?.telegramMethodQueues;
  if (!state) {
    return [];
  }
  const now = Date.now();
  return [...state.floodWaits.values()].map((entry) => ({
    methodClass: entry.methodClass,
    priority: entry.priority,
    label: entry.label,
    retryAfter: entry.retryAfter,
    pausedForMs: Math.max(0, entry.pausedUntilMs - now),
    lastError: entry.lastError,
  }));
}
