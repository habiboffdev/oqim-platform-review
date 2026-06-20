import { json } from './http-utils.js';

export function parseFloodWaitSeconds(err) {
  if (typeof err?.seconds === 'number' && Number.isFinite(err.seconds)) {
    return Math.max(1, err.seconds);
  }
  const match = /FLOOD_WAIT_(\d+)/.exec(err?.message || '');
  if (!match) return null;
  return Math.max(1, parseInt(match[1], 10) || 0);
}

export function telegramApiError(res, err, fallbackMessage) {
  const retryAfter = parseFloodWaitSeconds(err);
  if (retryAfter) {
    return json(res, 429, {
      error: 'Rate limited',
      retryAfter,
    });
  }
  return json(res, 502, { error: fallbackMessage });
}

export function normalizeTelegramAuthError(err) {
  const rawMessage = String(err?.message || err || 'Telegram auth failed');
  const upperMessage = rawMessage.toUpperCase();

  if (upperMessage.includes('AUTH_TOKEN_EXPIRED')) {
    return {
      code: 'AUTH_TOKEN_EXPIRED',
      message: 'QR code expired. Generate a new QR code.',
      retryable: true,
    };
  }

  if (upperMessage.includes('PASSWORD_HASH_INVALID')) {
    return {
      code: 'PASSWORD_HASH_INVALID',
      message: '2FA password is incorrect.',
      retryable: true,
    };
  }

  if (upperMessage.includes('2FA_TIMEOUT')) {
    return {
      code: '2FA_TIMEOUT',
      message: '2FA password timed out.',
      retryable: true,
    };
  }

  if (
    upperMessage.includes('AUTH_KEY_UNREGISTERED')
    || upperMessage.includes('SESSION_REVOKED')
    || upperMessage.includes('USER_DEACTIVATED')
  ) {
    return {
      code: 'SESSION_REVOKED',
      message: 'Telegram session was revoked. Reconnect the account.',
      retryable: true,
    };
  }

  return {
    code: 'TELEGRAM_AUTH_FAILED',
    message: rawMessage,
    retryable: false,
  };
}

export function normalizeTelegramPhoneAuthError(err) {
  const rawMessage = String(err?.message || err || 'Telegram phone auth failed');
  const upperMessage = rawMessage.toUpperCase();
  const retryAfter = parseFloodWaitSeconds(err);

  if (retryAfter) {
    return {
      error: 'RATE_LIMITED',
      code: 'RATE_LIMITED',
      message: 'Telegram is rate limiting this login attempt.',
      retryable: true,
      retryAfter,
    };
  }

  if (upperMessage.includes('PHONE_NUMBER_INVALID')) {
    return {
      error: 'PHONE_NUMBER_INVALID',
      code: 'PHONE_NUMBER_INVALID',
      message: 'The phone number is not valid for Telegram.',
      retryable: true,
    };
  }

  if (upperMessage.includes('PHONE_NUMBER_BANNED')) {
    return {
      error: 'PHONE_NUMBER_BANNED',
      code: 'PHONE_NUMBER_BANNED',
      message: 'Telegram rejected this phone number.',
      retryable: false,
    };
  }

  if (
    upperMessage.includes('REQUEST WAS UNSUCCESSFUL')
    || upperMessage.includes('TIMEOUT')
    || upperMessage.includes('CONNECT_TIMEOUT')
    || upperMessage.includes('PHONE MIGRATED')
  ) {
    return {
      error: 'PHONE_CODE_SEND_FAILED',
      code: 'PHONE_CODE_SEND_FAILED',
      message: 'Telegram did not send the login code. Try QR login or retry shortly.',
      retryable: true,
    };
  }

  return {
    error: 'PHONE_CODE_SEND_FAILED',
    code: 'PHONE_CODE_SEND_FAILED',
    message: 'Telegram did not send the login code. Try QR login or retry shortly.',
    retryable: true,
  };
}
