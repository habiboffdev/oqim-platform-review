import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  applyHotMessageState,
  createTelegramStateStore,
  rememberPeer,
  telegramStateCounts,
} from './telegram-state-store.js';

describe('telegram state store', () => {
  it('normalizes user and chat entities without Telegram RPCs', () => {
    const state = createTelegramStateStore();

    rememberPeer(state, {
      id: 100,
      firstName: 'Ali',
      lastName: 'Karimov',
      username: 'ali',
      bot: false,
    }, 123.4);
    rememberPeer(state, {
      id: 200,
      title: 'SATStation',
      broadcast: true,
      username: 'satstation',
    }, 123.5);

    assert.equal(state.users.get('100').firstName, 'Ali');
    assert.equal(state.users.get('100').updatedAt, 123.4);
    assert.equal(state.chats.get('200').title, 'SATStation');
    assert.equal(state.chats.get('200').broadcast, true);
  });

  it('applies hot message state before background enrichment', () => {
    const runtime = { workspaceId: 7 };
    const appliedAt = applyHotMessageState(runtime, {
      id: 55,
      chatId: 444,
      senderId: 555,
      date: 1_700_000_000,
      message: 'bor',
      out: false,
      sender: { id: 555, firstName: 'Operator' },
    }, {
      telegramUpdateReceivedAt: 123.6,
    });

    assert.equal(runtime.telegramState.updateState.lastReceivedAt, 123.6);
    assert.equal(runtime.telegramState.updateState.lastAppliedAt, appliedAt);
    assert.deepEqual(telegramStateCounts(runtime), {
      users: 1,
      chats: 0,
      messages: 1,
    });
    assert.equal(runtime.telegramState.messages.get('444:55').text, 'bor');
  });

  it('captures the resolved peer when gramjs cache leaves msg.chat/sender empty (#417)', () => {
    const runtime = { workspaceId: 7 };
    // First-contact customer: cold gramjs cache, so msg.chat/msg.sender are
    // empty. The caller already resolved the entity for the human filter.
    applyHotMessageState(runtime, {
      id: 60,
      isPrivate: true,
      chatId: 1016256593,
      senderId: 1016256593,
      date: 1_700_000_000,
      message: 'salom',
      out: false,
    }, {
      telegramUpdateReceivedAt: 200.0,
      resolvedPeer: {
        id: 1016256593,
        firstName: 'Jasur',
        accessHash: '8888888888888888888',
      },
    });

    const user = runtime.telegramState.users.get('1016256593');
    assert.ok(user, 'new customer peer must be remembered for first-contact replies');
    assert.equal(user.accessHash, '8888888888888888888');
  });
});
