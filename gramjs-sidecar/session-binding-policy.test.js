import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isHealthyWorkspaceSession,
  telegramUserIdsMatch,
} from './session-binding-policy.js';

test('telegramUserIdsMatch compares numeric and string Telegram ids safely', () => {
  assert.equal(telegramUserIdsMatch(12345, '12345'), true);
  assert.equal(telegramUserIdsMatch('12345', 12345), true);
  assert.equal(telegramUserIdsMatch('12345', '54321'), false);
  assert.equal(telegramUserIdsMatch(null, '12345'), false);
});

test('isHealthyWorkspaceSession requires a connected runtime with a client', () => {
  assert.equal(isHealthyWorkspaceSession({ client: {}, connectionState: 'connected' }), true);
  assert.equal(isHealthyWorkspaceSession({ client: null, connectionState: 'connected' }), false);
  assert.equal(isHealthyWorkspaceSession({ client: {}, connectionState: 'disconnected' }), false);
});

