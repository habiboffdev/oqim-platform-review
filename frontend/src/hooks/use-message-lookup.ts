import { useMemo } from 'react'
import type { ChatItem } from '@/components/blocks/chat/message-list'
import type { Message } from '@/lib/types'

export function useMessageLookup(
  chatItems: ChatItem[],
): Map<number, { message: Message; chatItemIndex: number }> {
  return useMemo(() => {
    const map = new Map<number, { message: Message; chatItemIndex: number }>()
    for (let i = 0; i < chatItems.length; i++) {
      const item = chatItems[i]
      if (
        item.type === 'message'
        && item.message.telegram_message_id != null
      ) {
        map.set(item.message.telegram_message_id, {
          message: item.message,
          chatItemIndex: i,
        })
      }
    }
    return map
  }, [chatItems])
}
