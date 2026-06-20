import 'telegram';
import { ConnectionTCPObfuscated } from 'telegram/network/connection/TCPObfuscated.js';

import { TelegramWebSockets } from './telegram-web-dc.js';

export function normalizeTelegramTransport(value, fallback = 'web') {
  const raw = String(value || fallback || 'web').trim().toLowerCase();
  return raw === 'tcp' ? 'tcp' : 'web';
}

export function resolveTelegramAuthTransport(env = process.env, requested = null) {
  return normalizeTelegramTransport(requested || env.TELEGRAM_AUTH_TRANSPORT || 'web');
}

export function buildWebKAuthClientParams(env = process.env) {
  return {
    deviceModel: env.TELEGRAM_AUTH_DEVICE_MODEL
      || 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
    systemVersion: env.TELEGRAM_AUTH_SYSTEM_VERSION || 'MacIntel',
    appVersion: env.TELEGRAM_AUTH_APP_VERSION || '2.2',
    langCode: env.TELEGRAM_AUTH_LANG_CODE || 'en',
    systemLangCode: env.TELEGRAM_AUTH_SYSTEM_LANG_CODE || env.TELEGRAM_AUTH_LANG_CODE || 'en',
    langPack: env.TELEGRAM_AUTH_LANG_PACK || 'webk',
  };
}

export function buildTelegramClientOptions({
  connectionRetries = 5,
  autoReconnect = true,
  timeoutSeconds = 10,
  transport = 'web',
  clientProfile = null,
  env = process.env,
} = {}) {
  const options = {
    connectionRetries,
    autoReconnect,
    floodSleepThreshold: 60,
    timeout: timeoutSeconds,
  };
  if (clientProfile === 'webk') {
    const { langPack, ...webKParams } = buildWebKAuthClientParams(env);
    Object.assign(options, webKParams);
  }

  if (normalizeTelegramTransport(transport) !== 'tcp') {
    options.useWSS = true;
    options.connection = ConnectionTCPObfuscated;
    options.networkSocket = TelegramWebSockets;
  }

  return options;
}

export function applyTelegramClientProfile(client, {
  clientProfile = null,
  env = process.env,
} = {}) {
  if (clientProfile !== 'webk' || !client?._initRequest) {
    return client;
  }
  const { langPack } = buildWebKAuthClientParams(env);
  client._initRequest.langPack = langPack;
  return client;
}
