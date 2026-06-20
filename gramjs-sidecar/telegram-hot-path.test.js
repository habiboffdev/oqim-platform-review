import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  cachedPrivatePeer,
  buildHotInboundEvent,
  shouldForwardHotInboundMessage,
} from './telegram-hot-path.js';

describe('telegram hot path', () => {
  it('builds an inbound event from cache-only message fields', () => {
    let getChatCalls = 0;
    let getSenderCalls = 0;
    const msg = {
      id: 10,
      isPrivate: true,
      chatId: 555,
      senderId: 777,
      message: 'salom',
      date: 1_700_000_000,
      out: false,
      sender: { id: 777, firstName: 'Ali', bot: false, self: false },
      getChat: async () => {
        getChatCalls += 1;
        throw new Error('hot path must not fetch chat');
      },
      getSender: async () => {
        getSenderCalls += 1;
        throw new Error('hot path must not fetch sender');
      },
    };

    const event = buildHotInboundEvent({
      runtime: {
        workspaceId: 3,
        latestMe: { id: 111 },
      },
      msg,
      telegramUpdateReceivedAt: 123.4,
      telegramStateAppliedAt: 123.45,
      hotEventBuiltAt: 123.5,
      connectorTelemetry: {
        telegram_connector_decision: 'forward',
        telegram_connector_gap_detected: false,
      },
    });

    assert.equal(getChatCalls, 0);
    assert.equal(getSenderCalls, 0);
    assert.equal(event.eventType, 'msg.inbound');
    assert.equal(event.payload.workspaceId, 3);
    assert.equal(event.payload.sellerUserId, '111');
    assert.equal(event.payload.chatId, '555');
    assert.equal(event.payload.senderId, '777');
    assert.equal(event.payload.senderName, 'Ali');
    assert.equal(event.payload.telegram_update_received_at, 123.4);
    assert.equal(event.payload.telegram_state_applied_at, 123.45);
    assert.equal(event.payload.hot_event_built_at, 123.5);
    assert.equal(event.payload.telegram_connector_decision, 'forward');
    assert.equal(event.payload.telegram_connector_gap_detected, false);
  });

  it('drops non-private and cached bot peers without RPC enrichment', () => {
    assert.equal(
      shouldForwardHotInboundMessage({
        msg: { isPrivate: false, chatId: 1, id: 1 },
        meId: 111,
      }),
      false,
    );

    assert.equal(
      shouldForwardHotInboundMessage({
        msg: {
          isPrivate: true,
          chatId: 2,
          id: 2,
          chat: { id: 2, bot: true, self: false },
        },
        meId: 111,
      }),
      false,
    );
  });

  it('allows cache-missing private user messages so enrichment can run later', () => {
    assert.equal(
      shouldForwardHotInboundMessage({
        msg: { isPrivate: true, chatId: 777, senderId: 777, id: 3 },
        meId: 111,
      }),
      true,
    );
  });
});

describe('cachedPrivatePeer', () => {
  it('returns null when no entity is cached (unknown peer must be resolved)', () => {
    assert.equal(cachedPrivatePeer({ isPrivate: true, chatId: 1 }), null);
    assert.equal(cachedPrivatePeer({ isPrivate: false, chat: { id: 1 } }), null);
  });

  it('returns the cached chat or inbound sender', () => {
    const chat = { id: 9, bot: true };
    assert.equal(cachedPrivatePeer({ isPrivate: true, chat }), chat);
    const sender = { id: 4 };
    assert.equal(cachedPrivatePeer({ isPrivate: true, out: false, sender }), sender);
    assert.equal(cachedPrivatePeer({ isPrivate: true, out: true, sender }), null);
  });
});

describe('senderUsername in hot inbound events', () => {
  it('carries the inbound sender username and nulls it for outgoing', async () => {
    const { buildHotInboundEvent } = await import('./telegram-hot-path.js');
    const runtime = { workspaceId: 1, latestMe: { id: 111 } };
    const inbound = buildHotInboundEvent({
      runtime,
      msg: {
        isPrivate: true,
        chatId: 555,
        senderId: 555,
        id: 10,
        message: 'salom',
        date: 1,
        sender: { id: 555, firstName: 'Jasur', username: 'jasur_biz' },
      },
    });
    assert.equal(inbound.payload.senderUsername, 'jasur_biz');

    const outgoing = buildHotInboundEvent({
      runtime,
      msg: {
        isPrivate: true,
        chatId: 555,
        senderId: 111,
        id: 11,
        out: true,
        message: 'javob',
        date: 2,
        chat: { id: 555, firstName: 'Jasur', username: 'jasur_biz' },
      },
    });
    assert.equal(outgoing.payload.senderUsername, null);
  });
});

describe('resolvedPeer fallback (cold gramjs cache)', () => {
  it('fills senderName and senderUsername from the resolved entity', async () => {
    const { buildHotInboundEvent } = await import('./telegram-hot-path.js');
    const event = buildHotInboundEvent({
      runtime: { workspaceId: 1, latestMe: { id: 111 } },
      msg: { isPrivate: true, chatId: 555, senderId: 555, id: 12, message: 'salom', date: 3 },
      resolvedPeer: { id: 555, firstName: 'Jasur', username: 'jasur_biz' },
    });
    assert.equal(event.payload.senderName, 'Jasur');
    assert.equal(event.payload.senderUsername, 'jasur_biz');
  });
});
