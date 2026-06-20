import { useState, useCallback } from 'react'
import { PaperPlaneRight } from '@phosphor-icons/react'
import { toast } from 'sonner'
import { useSendMessage } from '@/hooks/use-send-message'
import { uz } from '@/lib/uz'

interface ComposeBoxProps {
  conversationId: number
  onMessageSent?: () => void
}

export function ComposeBox({ conversationId, onMessageSent }: ComposeBoxProps) {
  const [text, setText] = useState('')
  const sendMessage = useSendMessage()

  const handleSend = useCallback(async () => {
    const trimmed = text.trim()
    if (!trimmed || sendMessage.isPending) return

    setText('')
    try {
      await sendMessage.mutateAsync({
        conversationId,
        content: trimmed,
      })
      onMessageSent?.()
    } catch {
      setText(trimmed)
      toast.error(uz.common.error)
    }
  }, [text, sendMessage, conversationId, onMessageSent])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  return (
    <div className="flex items-end gap-2 border-t bg-background px-4 py-3">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={uz.conversations.composePlaceholder ?? "Xabar yozing..."}
        rows={1}
        disabled={sendMessage.isPending}
        className="min-h-[36px] max-h-[120px] flex-1 resize-none rounded-lg border bg-muted/30 px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring"
      />
      <button
        onClick={handleSend}
        disabled={!text.trim() || sendMessage.isPending}
        className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-foreground text-background transition-opacity disabled:opacity-30"
      >
        <PaperPlaneRight weight="thin" className="size-4" />
      </button>
    </div>
  )
}
