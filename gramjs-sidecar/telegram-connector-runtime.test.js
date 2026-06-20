import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  channelCursorIdForMessage,
  connectorCursorForMessage,
  connectorMessageKey,
  connectorRuntimeStatus,
  prepareInboundConnectorEvent,
} from './telegram-connector-runtime.js';

describe('telegram connector runtime gate', () => {
  it('deduplicates repeated live messages inside one runtime before backend enqueue', () => {
    const runtime = { workspaceId: 7 };
    const msg = {
      id: 10,
      chatId: 555,
      senderId: 555,
      date: 100,
      pts: 20,
    };

    const first = prepareInboundConnectorEvent({ runtime, msg, nowSeconds: 1000 });
    const second = prepareInboundConnectorEvent({ runtime, msg, nowSeconds: 1001 });

    assert.equal(first.action, 'forward');
    assert.equal(second.action, 'skip');
    assert.equal(second.reason, 'duplicate_in_runtime');
    assert.equal(connectorRuntimeStatus(runtime).duplicatesSkipped, 1);
    assert.equal(connectorRuntimeStatus(runtime).seenMessages, 1);
  });

  it('detects update cursor gaps but still forwards the current live message', () => {
    const runtime = { workspaceId: 7 };
    let scheduled = 0;

    const first = prepareInboundConnectorEvent({
      runtime,
      msg: { id: 10, chatId: 555, date: 100, pts: 20 },
      nowSeconds: 1000,
      scheduleGapRepair: () => {
        scheduled += 1;
      },
    });
    const second = prepareInboundConnectorEvent({
      runtime,
      msg: { id: 11, chatId: 555, date: 101, pts: 25 },
      nowSeconds: 1001,
      scheduleGapRepair: () => {
        scheduled += 1;
      },
    });

    assert.equal(first.action, 'forward');
    assert.equal(second.action, 'forward');
    assert.equal(second.reason, 'forward_with_gap_repair');
    assert.equal(second.gapDetected, true);
    assert.equal(second.telemetry.telegram_connector_gap_detected, true);
    assert.equal(scheduled, 1);
    assert.equal(connectorRuntimeStatus(runtime).gapsDetected, 1);
    assert.deepEqual(connectorRuntimeStatus(runtime).lastGap, {
      cursorKey: 'global',
      previousPts: 20,
      currentPts: 25,
      channelId: '',
      messageKey: '7:555:11',
    });
  });

  it('uses global update cursor for private chats and scoped cursor for channels', () => {
    assert.equal(
      channelCursorIdForMessage({
        id: 1,
        chatId: 5924086090,
        chat: { id: 5924086090, firstName: 'Customer' },
      }),
      '',
    );
    assert.equal(
      channelCursorIdForMessage({
        id: 2,
        chatId: -100123,
        peerId: { channelId: 123n },
        chat: { id: 123n, title: 'SATStation', broadcast: true },
      }),
      '123',
    );

    assert.deepEqual(
      connectorCursorForMessage({
        id: 2,
        chatId: -100123,
        peerId: { channelId: 123n },
        date: 200,
        pts: 30,
      }),
      {
        scope: 'hot_path',
        channelId: '123',
        key: '123',
        pts: 30,
        seq: null,
        qts: null,
        telegramDate: 200,
      },
    );
  });

  it('builds stable message keys with workspace isolation', () => {
    assert.equal(
      connectorMessageKey(
        { workspaceId: 1 },
        { chatId: 5924086090, id: 1609 },
      ),
      '1:5924086090:1609',
    );
    assert.equal(
      connectorMessageKey(
        { workspaceId: 2 },
        { chatId: 5924086090, id: 1609 },
      ),
      '2:5924086090:1609',
    );
  });
});
