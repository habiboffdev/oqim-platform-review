import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  buildDeletedEvent,
  buildInboundEvent,
  parseByteRange,
  serializeBackfillMessage,
  serializeMediaMetadata,
  sniffMediaMime,
} from './telegram-codec.js';

describe('telegram-codec', () => {
  it('serializes inbound events with stable idempotency and metadata', () => {
    const event = buildInboundEvent({
      workspaceId: 7,
      sellerUserId: 111,
      chatId: { toString: () => '998' },
      senderId: { toString: () => '222' },
      senderName: 'Operator',
      msg: {
        id: 333,
        message: 'salom 💙',
        date: 1714072800,
        out: false,
        media: { className: 'MessageMediaDocument' },
        document: {
          mimeType: 'video/mp4',
          size: 12345,
          attributes: [
            { className: 'DocumentAttributeVideo', duration: 4, w: 240, h: 240, roundMessage: true },
          ],
          thumbs: [{ className: 'PhotoSize', w: 90, h: 90, size: 2048 }],
        },
        entities: [
          { className: 'MessageEntityCustomEmoji', offset: 6, length: 2, documentId: 999n },
        ],
        replyTo: { replyToMsgId: 222 },
        groupedId: { toString: () => '777777777777777777' },
      },
      isHistorical: true,
    });

    assert.equal(event.workspaceId, 7);
    assert.equal(event.eventType, 'msg.inbound');
    assert.equal(event.idempotencyKey, 'tg:998:333');
    assert.equal(event.path, '/api/webhook/telegram');
    assert.equal(event.payload.chatId, '998');
    assert.equal(event.payload.senderId, '222');
    assert.equal(event.payload.groupedId, '777777777777777777');
    assert.equal(event.payload.isHistorical, true);
    assert.deepEqual(event.payload.textEntities, [{
      type: 'custom_emoji',
      offset: 6,
      length: 2,
      documentId: '999',
    }]);
    assert.equal(event.payload.mediaMetadata.is_round, true);
    assert.equal(event.payload.mediaMetadata.length, 240);
    assert.equal(event.payload.mediaMetadata.has_thumbnail, true);
  });

  it('serializes backfill messages with the same grouped id normalization', () => {
    const payload = serializeBackfillMessage({
      id: 44,
      senderId: 10,
      message: 'photo',
      date: 1714072800,
      out: true,
      groupedId: { toString: () => '123456789012345678' },
    });

    assert.equal(payload.groupedId, '123456789012345678');
    assert.equal(payload.senderId, '10');
    assert.equal(payload.isOutgoing, true);
  });

  it('extracts photo and document media metadata without Telegram clients', () => {
    assert.deepEqual(
      serializeMediaMetadata({
        photo: {
          sizes: [
            { className: 'PhotoSize', w: 20, h: 20, size: 400 },
            { className: 'PhotoSize', w: 80, h: 80, size: 1600 },
          ],
        },
      }),
      {
        mime_type: 'image/jpeg',
        width: 80,
        height: 80,
        file_size: 1600,
        has_thumbnail: true,
        source: 'telegram',
      },
    );

    assert.deepEqual(
      serializeMediaMetadata({
        document: {
          mimeType: 'audio/ogg',
          size: 4096,
          attributes: [
            { className: 'DocumentAttributeAudio', duration: 12, waveform: Buffer.from([1, 2]) },
            { className: 'DocumentAttributeFilename', fileName: 'voice.ogg' },
          ],
        },
      }),
      {
        mime_type: 'audio/ogg',
        file_name: 'voice.ogg',
        file_size: 4096,
        duration: 12,
        is_round: false,
        waveform: [1, 2],
        is_animated: false,
        is_video: false,
        has_thumbnail: false,
        source: 'telegram',
      },
    );
  });

  it('parses byte ranges and rejects invalid ranges', () => {
    assert.deepEqual(parseByteRange('bytes=10-19', 100), {
      start: 10,
      end: 19,
      totalSize: 100,
    });
    assert.deepEqual(parseByteRange('bytes=-5', 100), {
      start: 95,
      end: 99,
      totalSize: 100,
    });
    assert.throws(() => parseByteRange('bytes=20-10', 100), /INVALID_RANGE/);
    assert.throws(() => parseByteRange('bytes=0-1,2-3', 100), /INVALID_RANGE/);
  });

  it('sniffs common media mime types for streaming responses', () => {
    assert.equal(sniffMediaMime(Buffer.from('474946383961', 'hex')), 'image/gif');
    assert.equal(sniffMediaMime(Buffer.from('89504e470d0a1a0a', 'hex')), 'image/png');
    assert.equal(sniffMediaMime(Buffer.from('0000002066747970', 'hex')), 'video/mp4');
  });

  it('sorts delete idempotency keys independent of event id order', () => {
    const event = buildDeletedEvent({
      workspaceId: 9,
      sellerUserId: 111,
      chatId: 333,
      messageIds: [9, 1, 5],
      deletedAt: 1714072800,
    });

    assert.equal(event.idempotencyKey, 'tg:333:del:1,5,9');
    assert.deepEqual(event.payload.messageIds, [9, 1, 5]);
  });
});
