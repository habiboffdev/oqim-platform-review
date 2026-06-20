import test from 'node:test';
import assert from 'node:assert/strict';

import { TelegramWebSockets, telegramWebDcIdForIp } from './telegram-web-dc.js';

test('telegram web dc mapper keeps migrated DC2 phone auth on kws2', () => {
  assert.equal(telegramWebDcIdForIp('149.154.167.41'), 2);
  assert.equal(telegramWebDcIdForIp('149.154.167.40'), 2);
  assert.equal(telegramWebDcIdForIp('149.154.167.50'), 2);
  assert.equal(telegramWebDcIdForIp('149.154.167.51'), 2);
});

test('telegram web socket url uses the migrated dc id instead of defaulting to dc4', () => {
  const socket = new TelegramWebSockets();

  assert.equal(
    socket.getWebSocketLink('149.154.167.41', 443),
    'wss://kws2.web.telegram.org/apiws',
  );
  assert.equal(
    socket.getWebSocketLink('149.154.167.41', 443, true),
    'wss://kws2.web.telegram.org/apiws_test',
  );
});
