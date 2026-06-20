import { once } from 'node:events';

import bigInt from 'big-integer';

import {
  fallbackDownloadMime,
  fullMediaSize,
  parseByteRange,
  sniffMediaMime,
} from './telegram-codec.js';

export function createResponseWriter(res, mimeFallback, options = {}) {
  let wroteHeaders = false;
  let bytesWritten = 0;
  const {
    statusCode = 200,
    extraHeaders = {},
    sniffContentType = true,
  } = options;

  return {
    async write(chunk) {
      if (!chunk?.length) return;
      if (res.destroyed || res.writableEnded) {
        throw new Error('CLIENT_ABORTED');
      }

      if (!wroteHeaders) {
        res.writeHead(statusCode, {
          'Content-Type': sniffContentType ? sniffMediaMime(chunk, mimeFallback) : mimeFallback,
          'Cache-Control': 'private, max-age=86400',
          ...extraHeaders,
        });
        wroteHeaders = true;
      }

      bytesWritten += chunk.length;
      if (!res.write(chunk)) {
        await once(res, 'drain');
      }
    },
    close() {
      if (wroteHeaders && !res.writableEnded && !res.destroyed) {
        res.end();
      }
    },
    get bytesWritten() {
      return bytesWritten;
    },
  };
}

export async function streamMediaRange(mediaClient, message, range, res) {
  const totalSize = fullMediaSize(message);
  const parsed = parseByteRange(range, totalSize);
  const mime = fallbackDownloadMime(message, false);
  const length = parsed.end - parsed.start + 1;
  const writer = createResponseWriter(res, mime, {
    statusCode: 206,
    sniffContentType: false,
    extraHeaders: {
      'Accept-Ranges': 'bytes',
      'Content-Range': `bytes ${parsed.start}-${parsed.end}/${parsed.totalSize}`,
      'Content-Length': String(length),
    },
  });

  let remaining = length;
  const requestSize = 256 * 1024;
  const limit = Math.ceil(length / requestSize);
  try {
    for await (const chunk of mediaClient.iterDownload({
      file: message.media,
      offset: bigInt(parsed.start),
      limit,
      requestSize,
    })) {
      if (remaining <= 0) break;
      const slice = chunk.length > remaining ? chunk.subarray(0, remaining) : chunk;
      await writer.write(slice);
      remaining -= slice.length;
    }
  } finally {
    writer.close();
  }

  return writer.bytesWritten > 0;
}
