import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeTelegramAuthError,
  normalizeTelegramPhoneAuthError,
  parseFloodWaitSeconds,
  telegramApiError,
} from './telegram-errors.js';

function responseRecorder() {
  return {
    status: null,
    body: null,
    writeHead(status) {
      this.status = status;
    },
    end(body) {
      this.body = body;
    },
  };
}

test('parseFloodWaitSeconds reads Telegram seconds and message codes', () => {
  assert.equal(parseFloodWaitSeconds({ seconds: 12 }), 12);
  assert.equal(parseFloodWaitSeconds({ seconds: 0 }), 1);
  assert.equal(parseFloodWaitSeconds({ message: 'FLOOD_WAIT_44' }), 44);
  assert.equal(parseFloodWaitSeconds({ message: 'PHONE_CODE_INVALID' }), null);
  assert.equal(parseFloodWaitSeconds(null), null);
});

test('telegramApiError returns retryable rate-limit responses', () => {
  const res = responseRecorder();

  telegramApiError(res, { message: 'FLOOD_WAIT_9' }, 'Send failed');

  assert.equal(res.status, 429);
  assert.equal(res.body, '{"error":"Rate limited","retryAfter":9}');
});

test('telegramApiError returns stable fallback responses for non-rate-limit errors', () => {
  const res = responseRecorder();

  telegramApiError(res, { message: 'CHAT_WRITE_FORBIDDEN' }, 'Telegram send failed');

  assert.equal(res.status, 502);
  assert.equal(res.body, '{"error":"Telegram send failed"}');
});

test('normalizeTelegramAuthError exposes retryable QR expiration safely', () => {
  assert.deepEqual(
    normalizeTelegramAuthError({
      message: '400: AUTH_TOKEN_EXPIRED (caused by auth.ImportLoginToken)',
    }),
    {
      code: 'AUTH_TOKEN_EXPIRED',
      message: 'QR code expired. Generate a new QR code.',
      retryable: true,
    },
  );
});

test('normalizeTelegramAuthError exposes retryable 2FA failures safely', () => {
  assert.deepEqual(
    normalizeTelegramAuthError({ message: 'PASSWORD_HASH_INVALID' }),
    {
      code: 'PASSWORD_HASH_INVALID',
      message: '2FA password is incorrect.',
      retryable: true,
    },
  );
});

test('normalizeTelegramAuthError exposes revoked sessions as reconnectable', () => {
  assert.deepEqual(
    normalizeTelegramAuthError({ message: 'AUTH_KEY_UNREGISTERED' }),
    {
      code: 'SESSION_REVOKED',
      message: 'Telegram session was revoked. Reconnect the account.',
      retryable: true,
    },
  );
});

test('normalizeTelegramPhoneAuthError hides GramJS retry internals behind stable codes', () => {
  assert.deepEqual(
    normalizeTelegramPhoneAuthError(new Error('Request was unsuccessful 5 time(s)')),
    {
      error: 'PHONE_CODE_SEND_FAILED',
      code: 'PHONE_CODE_SEND_FAILED',
      message: 'Telegram did not send the login code. Try QR login or retry shortly.',
      retryable: true,
    },
  );
});

test('normalizeTelegramPhoneAuthError preserves phone and rate-limit decisions', () => {
  assert.deepEqual(
    normalizeTelegramPhoneAuthError(new Error('PHONE_NUMBER_INVALID')),
    {
      error: 'PHONE_NUMBER_INVALID',
      code: 'PHONE_NUMBER_INVALID',
      message: 'The phone number is not valid for Telegram.',
      retryable: true,
    },
  );
  assert.deepEqual(
    normalizeTelegramPhoneAuthError({ message: 'FLOOD_WAIT_9' }),
    {
      error: 'RATE_LIMITED',
      code: 'RATE_LIMITED',
      message: 'Telegram is rate limiting this login attempt.',
      retryable: true,
      retryAfter: 9,
    },
  );
});
