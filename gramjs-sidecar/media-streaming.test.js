import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { createResponseWriter, streamMediaRange } from './media-streaming.js';

function responseRecorder() {
  const res = new EventEmitter();
  res.destroyed = false;
  res.writableEnded = false;
  res.statusCode = null;
  res.headers = null;
  res.chunks = [];
  res.ended = false;
  res.writeHead = (statusCode, headers) => {
    res.statusCode = statusCode;
    res.headers = headers;
  };
  res.write = (chunk) => {
    res.chunks.push(Buffer.from(chunk));
    return true;
  };
  res.end = () => {
    res.ended = true;
    res.writableEnded = true;
  };
  return res;
}

test('createResponseWriter writes headers lazily and sniffs images', async () => {
  const res = responseRecorder();
  const writer = createResponseWriter(res, 'application/octet-stream');

  await writer.write(Buffer.from([0xff, 0xd8, 0xff, 0x00]));
  writer.close();

  assert.equal(res.statusCode, 200);
  assert.equal(res.headers['Content-Type'], 'image/jpeg');
  assert.equal(res.headers['Cache-Control'], 'private, max-age=86400');
  assert.equal(writer.bytesWritten, 4);
  assert.equal(res.ended, true);
});

test('streamMediaRange writes bounded partial content', async () => {
  const res = responseRecorder();
  const calls = [];
  const mediaClient = {
    async *iterDownload(options) {
      calls.push(options);
      yield Buffer.from('cdefgh');
    },
  };
  const message = {
    media: { document: { mimeType: 'video/mp4' } },
    document: { size: 10 },
  };

  const streamed = await streamMediaRange(mediaClient, message, 'bytes=2-5', res);

  assert.equal(streamed, true);
  assert.equal(res.statusCode, 206);
  assert.equal(res.headers['Content-Type'], 'video/mp4');
  assert.equal(res.headers['Content-Range'], 'bytes 2-5/10');
  assert.equal(res.headers['Content-Length'], '4');
  assert.equal(Buffer.concat(res.chunks).toString('utf8'), 'cdef');
  assert.equal(String(calls[0].offset), '2');
  assert.equal(calls[0].requestSize, 256 * 1024);
  assert.equal(res.ended, true);
});
