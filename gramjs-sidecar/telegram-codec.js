const MEDIA_MAGIC_BYTES = [
  { prefix: Buffer.from('52494646', 'hex'), offset: 0, suffix: Buffer.from('57454250', 'hex'), suffixOffset: 8, mime: 'image/webp' },
  { prefix: Buffer.from('ffd8ff', 'hex'), offset: 0, mime: 'image/jpeg' },
  { prefix: Buffer.from('89504e47', 'hex'), offset: 0, mime: 'image/png' },
  { prefix: Buffer.from('47494638', 'hex'), offset: 0, mime: 'image/gif' },
  { prefix: Buffer.from('1a45dfa3', 'hex'), offset: 0, mime: 'video/webm' },
  { prefix: Buffer.from('4f676753', 'hex'), offset: 0, mime: 'audio/ogg' },
  { prefix: Buffer.from('000000', 'hex'), offset: 0, suffix: Buffer.from('66747970', 'hex'), suffixOffset: 4, mime: 'video/mp4' },
];

export function normalizePeerId(value) {
  if (value == null) return null;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'object' && 'value' in value) return Number(value.value);
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function safeNumber(value) {
  if (value == null) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value.toJSNumber === 'function') return value.toJSNumber();
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function cleanMetadata(metadata) {
  const entries = Object.entries(metadata).filter(([, value]) => {
    if (value == null) return false;
    if (Array.isArray(value) && value.length === 0) return false;
    return true;
  });
  return entries.length ? Object.fromEntries(entries) : null;
}

export function sniffMediaMime(buffer, fallback = 'application/octet-stream') {
  for (const signature of MEDIA_MAGIC_BYTES) {
    const { prefix, offset, suffix, suffixOffset, mime } = signature;
    if (buffer.length < offset + prefix.length) continue;
    if (!buffer.subarray(offset, offset + prefix.length).equals(prefix)) continue;
    if (suffix) {
      if (buffer.length < suffixOffset + suffix.length) continue;
      if (!buffer.subarray(suffixOffset, suffixOffset + suffix.length).equals(suffix)) continue;
    }
    return mime;
  }
  return fallback;
}

export function findDocumentAttribute(document, className) {
  return document?.attributes?.find((attr) => attr?.className === className) || null;
}

export function sortPhotoSizeValue(size) {
  if (!size) return 0;
  if (size.className === 'PhotoCachedSize' && size.bytes) return size.bytes.length;
  if (size.className === 'PhotoStrippedSize' && size.bytes) return size.bytes.length;
  if (size.className === 'PhotoSize' && typeof size.size === 'number') return size.size;
  if (size.className === 'PhotoSizeProgressive' && Array.isArray(size.sizes)) {
    return Math.max(...size.sizes);
  }
  if (size.className === 'VideoSize' && typeof size.size === 'number') return size.size;
  return 0;
}

export function selectPreferredThumb(mediaOwner) {
  const thumbs = mediaOwner?.thumbs || mediaOwner?.sizes || [];
  const candidates = thumbs.filter(
    (size) => (
      size
      && size.className !== 'PhotoPathSize'
      && size.className !== 'PhotoSizeEmpty'
      && size.className !== 'PhotoStrippedSize'
    ),
  );
  if (!candidates.length) return null;
  return [...candidates].sort((a, b) => sortPhotoSizeValue(a) - sortPhotoSizeValue(b)).pop();
}

export function listThumbCandidates(mediaOwner) {
  const thumbs = mediaOwner?.thumbs || mediaOwner?.sizes || [];
  return [...thumbs]
    .filter((size) => (
      size
      && size.className !== 'PhotoPathSize'
      && size.className !== 'PhotoSizeEmpty'
    ))
    .sort((a, b) => sortPhotoSizeValue(b) - sortPhotoSizeValue(a));
}

export function fallbackDownloadMime(message, thumb) {
  if (thumb) {
    if (message?.photo) return 'image/jpeg';
    if (message?.document?.mimeType === 'application/x-tgsticker') return 'image/webp';
  }

  return (
    message?.media?.document?.mimeType
    || (message?.media?.photo ? 'image/jpeg' : 'application/octet-stream')
  );
}

export function fullMediaSize(message) {
  const documentSize = safeNumber(message?.document?.size);
  if (documentSize != null && documentSize > 0) return documentSize;

  const photo = message?.photo;
  if (!photo) return null;
  const bestSize = selectPreferredThumb(photo);
  if (!bestSize) return null;
  if (bestSize.className === 'PhotoSizeProgressive' && Array.isArray(bestSize.sizes)) {
    return Math.max(...bestSize.sizes);
  }
  return safeNumber(bestSize.size) || safeNumber(bestSize.bytes?.length);
}

export function parseByteRange(rangeHeader, totalSize) {
  if (!rangeHeader) return null;
  if (!Number.isFinite(totalSize) || totalSize <= 0) {
    throw new Error('INVALID_RANGE');
  }
  if (!rangeHeader.startsWith('bytes=')) {
    throw new Error('INVALID_RANGE');
  }

  const rangeSpec = rangeHeader.slice(6);
  if (!rangeSpec || rangeSpec.includes(',')) {
    throw new Error('INVALID_RANGE');
  }
  if (rangeSpec.indexOf('-') !== rangeSpec.lastIndexOf('-')) {
    throw new Error('INVALID_RANGE');
  }

  const [startText, endText] = rangeSpec.split('-', 2);
  let start;
  let end;

  if (startText) {
    start = Number(startText);
    end = endText ? Number(endText) : totalSize - 1;
  } else if (endText) {
    const suffixLength = Number(endText);
    if (!Number.isInteger(suffixLength) || suffixLength <= 0) {
      throw new Error('INVALID_RANGE');
    }
    start = Math.max(totalSize - suffixLength, 0);
    end = totalSize - 1;
  } else {
    throw new Error('INVALID_RANGE');
  }

  if (
    !Number.isInteger(start)
    || !Number.isInteger(end)
    || start < 0
    || start >= totalSize
    || end < start
    || end >= totalSize
  ) {
    throw new Error('INVALID_RANGE');
  }

  return {
    start,
    end,
    totalSize,
  };
}

export function encodeWaveform(waveform) {
  if (!waveform) return undefined;
  try {
    return Array.from(Buffer.from(waveform));
  } catch {
    return undefined;
  }
}

export function serializeMessageEntities(msg) {
  const entities = Array.isArray(msg?.entities) ? msg.entities : [];
  return entities.flatMap((entity) => {
    if (!entity || typeof entity.offset !== 'number' || typeof entity.length !== 'number') {
      return [];
    }
    if (entity.className === 'MessageEntityCustomEmoji' && entity.documentId) {
      return [{
        type: 'custom_emoji',
        offset: entity.offset,
        length: entity.length,
        documentId: String(entity.documentId),
      }];
    }
    return [];
  });
}

export function serializeMediaMetadata(msg) {
  const photo = msg?.photo;
  if (photo) {
    const bestSize = selectPreferredThumb(photo);
    return cleanMetadata({
      mime_type: 'image/jpeg',
      width: bestSize?.w,
      height: bestSize?.h,
      file_size: bestSize?.size || (Array.isArray(bestSize?.sizes) ? Math.max(...bestSize.sizes) : undefined),
      has_thumbnail: Boolean(bestSize),
      source: 'telegram',
    });
  }

  const document = msg?.document;
  if (!document) {
    return null;
  }

  const audioAttr = findDocumentAttribute(document, 'DocumentAttributeAudio');
  const videoAttr = findDocumentAttribute(document, 'DocumentAttributeVideo');
  const imageAttr = findDocumentAttribute(document, 'DocumentAttributeImageSize');
  const fileNameAttr = findDocumentAttribute(document, 'DocumentAttributeFilename');
  const stickerAttr = findDocumentAttribute(document, 'DocumentAttributeSticker');
  const animatedAttr = findDocumentAttribute(document, 'DocumentAttributeAnimated');
  const hasThumb = Boolean(selectPreferredThumb(document));

  return cleanMetadata({
    mime_type: document.mimeType,
    file_name: fileNameAttr?.fileName,
    file_size: normalizePeerId(document.size),
    width: videoAttr?.w || imageAttr?.w,
    height: videoAttr?.h || imageAttr?.h,
    length: videoAttr?.roundMessage ? Math.min(videoAttr?.w || 0, videoAttr?.h || 0) || undefined : undefined,
    duration: audioAttr?.duration ?? videoAttr?.duration,
    is_round: Boolean(videoAttr?.roundMessage),
    performer: audioAttr?.performer,
    title: audioAttr?.title,
    waveform: encodeWaveform(audioAttr?.waveform),
    emoji: stickerAttr?.alt,
    is_animated: Boolean(animatedAttr),
    is_video: Boolean(videoAttr?.roundMessage || document.mimeType === 'video/webm'),
    has_thumbnail: hasThumb,
    source: 'telegram',
  });
}

export function serializeBackfillMessage(msg) {
  return {
    messageId: msg.id,
    senderId: String(msg.senderId || msg.fromId || msg.chatId || ''),
    text: msg.message || '',
    date: msg.date,
    isOutgoing: msg.out || false,
    mediaType: msg.media?.className || null,
    mediaMetadata: serializeMediaMetadata(msg),
    textEntities: serializeMessageEntities(msg),
    replyToMsgId: msg.replyTo?.replyToMsgId || null,
    groupedId: stringifyTelegramId(msg.groupedId),
  };
}

export function serializeChannelDialog(dialog) {
  const entity = dialog?.entity || {};
  const isBroadcast = Boolean(entity.broadcast);
  const isMegagroup = Boolean(entity.megagroup);
  if (!isBroadcast && !isMegagroup) return null;

  const id = normalizePeerId(dialog.id ?? entity.id);
  if (!id) return null;

  return {
    id,
    name: dialog.title || entity.title || `Channel ${id}`,
    member_count: entity.participantsCount ?? null,
    username: entity.username || undefined,
    is_broadcast: isBroadcast,
    is_own: Boolean(entity.creator || entity.adminRights),
  };
}

export function serializeChannelPost(msg) {
  return {
    text: msg?.message || '',
    date: msg?.date || 0,
    postId: msg?.id || 0,
    mediaType: msg?.media?.className || null,
  };
}

export function buildInboundEvent({
  workspaceId,
  sellerUserId,
  chatId,
  senderId,
  senderName,
  senderUsername = null,
  msg,
  isHistorical = false,
  telemetry = {},
}) {
  const normalizedChatId = String(chatId);
  const normalizedMessageId = String(msg.id);
  return {
    workspaceId,
    eventType: 'msg.inbound',
    idempotencyKey: `tg:${normalizedChatId}:${normalizedMessageId}`,
    path: '/api/webhook/telegram',
    payload: {
      sellerUserId: String(sellerUserId || ''),
      workspaceId: workspaceId ?? 0,
      chatId: normalizedChatId,
      senderId: String(senderId),
      senderName,
      senderUsername: senderUsername || null,
      messageId: msg.id,
      text: msg.message || '',
      date: msg.date,
      isOutgoing: msg.out || false,
      mediaType: msg.media?.className || null,
      mediaMetadata: serializeMediaMetadata(msg),
      textEntities: serializeMessageEntities(msg),
      replyToMsgId: msg.replyTo?.replyToMsgId || null,
      groupedId: stringifyTelegramId(msg.groupedId),
      isHistorical: Boolean(isHistorical),
      ...cleanMetadata(telemetry),
    },
  };
}

export function buildEditedEvent({
  workspaceId,
  sellerUserId,
  msg,
  editedAt,
}) {
  const chatId = msg.chatId ? String(msg.chatId) : null;
  return {
    workspaceId,
    eventType: 'msg.edited',
    idempotencyKey: `tg:${chatId}:${msg.id}:edit:${editedAt}`,
    path: '/api/webhook/telegram/message-edit',
    payload: {
      sellerUserId: String(sellerUserId),
      chatId,
      messageId: msg.id,
      text: msg.message || '',
      textEntities: serializeMessageEntities(msg),
      editedAt,
    },
  };
}

export function buildDeletedEvent({
  workspaceId,
  sellerUserId,
  chatId,
  messageIds,
  deletedAt,
}) {
  const normalizedChatId = chatId ? String(chatId) : null;
  const normalizedMessageIds = messageIds || [];
  return {
    workspaceId,
    eventType: 'msg.deleted',
    idempotencyKey: `tg:${normalizedChatId}:del:${normalizedMessageIds.map(String).sort().join(',')}`,
    path: '/api/webhook/telegram/message-delete',
    payload: {
      sellerUserId: String(sellerUserId),
      chatId: normalizedChatId,
      messageIds: normalizedMessageIds,
      deletedAt,
    },
  };
}

function stringifyTelegramId(value) {
  if (value == null) return null;
  return String(value);
}
