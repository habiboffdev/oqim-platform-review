import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  TELEGRAM_QUEUE_PAUSED,
  runQueuedTelegramMethod,
  telegramMethodQueueStatus,
} from './telegram-method-queue.js';

describe('telegram method queue', () => {
  it('pauses only the flooded runtime and method class', async () => {
    const runtimeA = {};
    const runtimeB = {};

    await assert.rejects(
      () => runQueuedTelegramMethod(
        runtimeA,
        { methodClass: 'dialog_sync', label: 'GET_DIALOGS_1', priority: 3 },
        async () => {
          throw Object.assign(new Error('FLOOD_WAIT_7'), { seconds: 7 });
        },
      ),
      /FLOOD_WAIT_7/,
    );

    await assert.rejects(
      () => runQueuedTelegramMethod(
        runtimeA,
        { methodClass: 'dialog_sync', label: 'GET_DIALOGS_1', priority: 3 },
        async () => 'should not run',
      ),
      (err) => err.code === TELEGRAM_QUEUE_PAUSED && err.methodClass === 'dialog_sync',
    );

    const liveResult = await runQueuedTelegramMethod(
      runtimeA,
      { methodClass: 'live_send', label: 'SEND_1', priority: 0 },
      async () => 'sent',
    );
    const otherTenantResult = await runQueuedTelegramMethod(
      runtimeB,
      { methodClass: 'dialog_sync', label: 'GET_DIALOGS_2', priority: 3 },
      async () => 'synced',
    );

    assert.equal(liveResult, 'sent');
    assert.equal(otherTenantResult, 'synced');
    assert.equal(telegramMethodQueueStatus(runtimeA)[0].methodClass, 'dialog_sync');
    assert.deepEqual(telegramMethodQueueStatus(runtimeB), []);
  });
});
