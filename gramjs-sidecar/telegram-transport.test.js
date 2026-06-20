import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyTelegramClientProfile,
  buildTelegramClientOptions,
  buildWebKAuthClientParams,
  normalizeTelegramTransport,
  resolveTelegramAuthTransport,
} from './telegram-transport.js';

test('auth transport can preserve old GramJS TCP login path', () => {
  const options = buildTelegramClientOptions({
    autoReconnect: false,
    connectionRetries: 1,
    timeoutSeconds: 6,
    transport: 'tcp',
  });

  assert.equal(options.autoReconnect, false);
  assert.equal(options.connectionRetries, 1);
  assert.equal(options.timeout, 6);
  assert.equal(options.useWSS, undefined);
  assert.equal(options.connection, undefined);
  assert.equal(options.networkSocket, undefined);
});

test('web transport keeps Telegram Web DC mapping for workspace sessions', () => {
  const options = buildTelegramClientOptions({ transport: 'web' });

  assert.equal(options.useWSS, true);
  assert.equal(typeof options.connection, 'function');
  assert.equal(typeof options.networkSocket, 'function');
});

test('unknown transport values fall back to web transport', () => {
  assert.equal(normalizeTelegramTransport('tcp'), 'tcp');
  assert.equal(normalizeTelegramTransport('web'), 'web');
  assert.equal(normalizeTelegramTransport('wat'), 'web');
  assert.equal(normalizeTelegramTransport('', 'tcp'), 'tcp');
});

test('phone auth transport defaults to Telegram Web transport', () => {
  assert.equal(resolveTelegramAuthTransport({}), 'web');
  assert.equal(resolveTelegramAuthTransport({ TELEGRAM_AUTH_TRANSPORT: '' }), 'web');
  assert.equal(resolveTelegramAuthTransport({ TELEGRAM_AUTH_TRANSPORT: 'tcp' }), 'tcp');
  assert.equal(resolveTelegramAuthTransport({}, 'tcp'), 'tcp');
  assert.equal(resolveTelegramAuthTransport({ TELEGRAM_AUTH_TRANSPORT: 'tcp' }, 'web'), 'web');
});

test('webk auth profile mirrors Telegram Web K init metadata', () => {
  const options = buildTelegramClientOptions({
    transport: 'web',
    clientProfile: 'webk',
    env: {
      TELEGRAM_AUTH_DEVICE_MODEL: 'Test UA',
      TELEGRAM_AUTH_SYSTEM_VERSION: 'Test Platform',
      TELEGRAM_AUTH_APP_VERSION: '2.2-test',
      TELEGRAM_AUTH_LANG_CODE: 'uz',
      TELEGRAM_AUTH_LANG_PACK: 'webk',
    },
  });

  assert.equal(options.deviceModel, 'Test UA');
  assert.equal(options.systemVersion, 'Test Platform');
  assert.equal(options.appVersion, '2.2-test');
  assert.equal(options.langCode, 'uz');
  assert.equal(options.systemLangCode, 'uz');
  assert.equal(options.langPack, undefined);
});

test('webk auth profile lang pack is applied to GramJS init request', () => {
  const client = { _initRequest: { langPack: '' } };

  applyTelegramClientProfile(client, {
    clientProfile: 'webk',
    env: { TELEGRAM_AUTH_LANG_PACK: 'webk' },
  });

  assert.equal(client._initRequest.langPack, 'webk');
});

test('webk auth profile has production defaults', () => {
  const params = buildWebKAuthClientParams({});

  assert.match(params.deviceModel, /Mozilla\/5\.0/);
  assert.equal(params.systemVersion, 'MacIntel');
  assert.equal(params.appVersion, '2.2');
  assert.equal(params.langCode, 'en');
  assert.equal(params.systemLangCode, 'en');
  assert.equal(params.langPack, 'webk');
});
