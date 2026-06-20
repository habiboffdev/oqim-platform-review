import assert from 'node:assert/strict';
import test from 'node:test';

import {
  shouldPromoteCatchUpMessageToLiveRecovery,
} from './telegram-live-recovery.js';

const nowSeconds = Date.parse('2026-06-05T20:20:00.000Z') / 1000;

test('fresh unread catch-up message is reply-capable after startup', () => {
  assert.equal(
    shouldPromoteCatchUpMessageToLiveRecovery(
      {
        workspaceId: 1,
        connectionState: 'connected',
        handlersRegisteredAt: '2026-06-05T20:14:39.650Z',
        lastLiveInboundHotPathAt: null,
      },
      {
        id: 10,
        out: false,
        date: nowSeconds - 30,
      },
      { nowSeconds },
    ),
    true,
  );
});

test('fresh catch-up promotes even when duplicate live health looks fresh', () => {
  assert.equal(
    shouldPromoteCatchUpMessageToLiveRecovery(
      {
        workspaceId: 1,
        connectionState: 'connected',
        handlersRegisteredAt: '2026-06-05T20:14:39.650Z',
        lastLiveInboundHotPathAt: '2026-06-05T20:19:30.000Z',
      },
      {
        id: 10,
        out: false,
        date: nowSeconds - 20,
      },
      { nowSeconds },
    ),
    true,
  );
});

test('old, outgoing, and malformed catch-up messages are not promoted', () => {
  const runtime = {
    workspaceId: 1,
    connectionState: 'connected',
    handlersRegisteredAt: '2026-06-05T20:14:39.650Z',
    lastLiveInboundHotPathAt: null,
  };

  assert.equal(
    shouldPromoteCatchUpMessageToLiveRecovery(
      runtime,
      { id: 10, out: false, date: nowSeconds - 900 },
      { nowSeconds },
    ),
    false,
  );
  assert.equal(
    shouldPromoteCatchUpMessageToLiveRecovery(
      runtime,
      { id: 10, out: true, date: nowSeconds - 30 },
      { nowSeconds },
    ),
    false,
  );
  assert.equal(
    shouldPromoteCatchUpMessageToLiveRecovery(
      runtime,
      { id: 10, out: false },
      { nowSeconds },
    ),
    false,
  );
});
