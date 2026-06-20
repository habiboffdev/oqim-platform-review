function entityDisplayName(entity) {
  if (!entity) return '';
  return [
    entity.firstName,
    entity.lastName,
  ].filter(Boolean).join(' ') || entity.title || entity.username || '';
}

export function isPrivateHumanEntity(entity, meId = null) {
  if (!entity) return false;
  if (entity.bot || entity.self) return false;
  if (entity.support || entity.deleted) return false;
  if (meId !== null && meId !== undefined && entity.id !== undefined && entity.id !== null) {
    if (String(entity.id) === String(meId)) return false;
  }
  return true;
}

export function isPrivateHumanDialog(dialog, meId = null) {
  if (!dialog?.isUser) return false;
  return isPrivateHumanEntity(dialog.entity, meId);
}

export function serializePrivateHumanDialog(dialog, meId = null) {
  if (!isPrivateHumanDialog(dialog, meId)) return null;
  return {
    chatId: String(dialog.id),
    title: dialog.title || dialog.name || entityDisplayName(dialog.entity),
    type: 'private',
    unreadCount: Number(dialog.unreadCount || 0),
    topMessageId: dialog.message?.id ? Number(dialog.message.id) : null,
    lastMessageText: dialog.message?.message || '',
    lastMessageDate: dialog.message?.date ? Number(dialog.message.date) : null,
    lastMessageIsOutgoing: Boolean(dialog.message?.out),
  };
}

export function isPrivateHumanMessageContext({ isPrivate, chat, meId = null }) {
  if (!isPrivate) return false;
  return isPrivateHumanEntity(chat, meId);
}

export function displayNameForMessage({ chat, sender, isOutgoing }) {
  if (isOutgoing) {
    return entityDisplayName(chat);
  }
  return entityDisplayName(sender) || entityDisplayName(chat);
}
