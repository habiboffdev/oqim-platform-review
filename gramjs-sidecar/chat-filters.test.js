import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  displayNameForMessage,
  isPrivateHumanDialog,
  isPrivateHumanMessageContext,
  serializePrivateHumanDialog,
} from './chat-filters.js';

describe('chat-filters', () => {
  it('keeps private human dialogs', () => {
    const dialog = {
      id: 42,
      isUser: true,
      unreadCount: 3,
      title: 'Ali',
      entity: { id: 42, firstName: 'Ali', bot: false, self: false },
      message: { id: 99 },
    };

    assert.equal(isPrivateHumanDialog(dialog, 1), true);
    assert.deepEqual(serializePrivateHumanDialog(dialog, 1), {
      chatId: '42',
      title: 'Ali',
      type: 'private',
      unreadCount: 3,
      topMessageId: 99,
      lastMessageText: '',
      lastMessageDate: null,
      lastMessageIsOutgoing: false,
    });
  });

  it('drops bot dialogs', () => {
    const dialog = {
      id: 77,
      isUser: true,
      unreadCount: 8,
      entity: { id: 77, firstName: 'HUMO', bot: true, self: false },
    };

    assert.equal(isPrivateHumanDialog(dialog, 1), false);
    assert.equal(serializePrivateHumanDialog(dialog, 1), null);
  });

  it('drops self chat', () => {
    const dialog = {
      id: 1,
      isUser: true,
      unreadCount: 1,
      entity: { id: 1, firstName: 'Me', bot: false, self: true },
    };

    assert.equal(isPrivateHumanDialog(dialog, 1), false);
  });

  it('drops Telegram support/service users', () => {
    const dialog = {
      id: 777000,
      isUser: true,
      unreadCount: 0,
      entity: { id: 777000, firstName: 'Telegram', bot: false, self: false, support: true },
    };

    assert.equal(isPrivateHumanDialog(dialog, 1), false);
  });

  it('drops non-private message contexts', () => {
    assert.equal(
      isPrivateHumanMessageContext({
        isPrivate: false,
        chat: { id: 42, bot: false, self: false },
        meId: 1,
      }),
      false,
    );
  });

  it('drops private bot message contexts', () => {
    assert.equal(
      isPrivateHumanMessageContext({
        isPrivate: true,
        chat: { id: 77, bot: true, self: false },
        meId: 1,
      }),
      false,
    );
  });

  it('uses peer display name for outgoing messages', () => {
    assert.equal(
      displayNameForMessage({
        isOutgoing: true,
        chat: { firstName: 'Ali', lastName: 'Valiyev' },
        sender: { firstName: 'Me' },
      }),
      'Ali Valiyev',
    );
  });
});
