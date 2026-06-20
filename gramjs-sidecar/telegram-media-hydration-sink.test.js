import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  buildMediaHydrationWebhookPayload,
  postHydratedMediaRefToBackend,
} from './telegram-media-hydration-sink.js';

const ref = {
  workspaceId: 7,
  chatId: '444',
  messageId: '55',
  mediaKey: 'document:999',
  mediaKind: 'MessageMediaDocument',
  documentId: '999',
  photoId: null,
  mimeType: 'image/png',
  size: 2048,
};

describe('telegram media hydration backend sink', () => {
  it('builds a stable sidecar webhook payload with base64 media content', () => {
    const payload = buildMediaHydrationWebhookPayload(ref, {
      content: Buffer.from('ok'),
      mediaType: 'image/png',
      downloadedAt: 1780000000.25,
    });

    assert.deepEqual(payload, {
      workspaceId: 7,
      chatId: '444',
      messageId: '55',
      mediaKey: 'document:999',
      mediaKind: 'MessageMediaDocument',
      documentId: '999',
      photoId: null,
      mimeType: 'image/png',
      size: 2048,
      contentBase64: 'b2s=',
      downloadedAt: 1780000000.25,
      source: 'sidecar_media_hydration',
    });
  });

  it('posts hydrated media refs through the existing backend webhook helper', async () => {
    const calls = [];

    await postHydratedMediaRefToBackend(
      {
        postJson: async (path, payload) => {
          calls.push({ path, payload });
          return { status: 'hydrated' };
        },
      },
      ref,
      {
        content: new Uint8Array([1, 2, 3]),
        mediaType: 'image/png',
        downloadedAt: 1780000001,
      },
    );

    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, '/api/webhook/telegram/media-hydration');
    assert.equal(calls[0].payload.contentBase64, 'AQID');
    assert.equal(calls[0].payload.workspaceId, 7);
  });
});
