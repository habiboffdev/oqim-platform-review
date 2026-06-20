// eslint-disable-next-line no-restricted-imports
// eslint-disable-next-line no-restricted-imports -- TODO: migrate to useMountEffect
import { useState, useRef, useEffect } from 'react'
import { motion } from 'framer-motion'
import { PencilSimple } from '@phosphor-icons/react'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'

interface ReplyBubbleProps {
  text: string
  index: number
  onEdit: (newText: string) => void
}

export function ReplyBubble({ text, index, onEdit }: ReplyBubbleProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [editText, setEditText] = useState(text)
  const cancelledRef = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isFirst = index === 0

  // TODO: migrate to event handler or TanStack Query
  // Sync prop → local state only when NOT editing
  useEffect(() => {
    if (!isEditing) {
      setEditText(text)
    }
  }, [text, isEditing])

  // TODO: migrate to event handler or TanStack Query
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus()
      textareaRef.current.select()
    }
  }, [isEditing])

  function handleSave() {
    if (cancelledRef.current) {
      cancelledRef.current = false
      return
    }
    const trimmed = editText.trim()
    if (trimmed && trimmed !== text) {
      onEdit(trimmed)
    }
    setIsEditing(false)
  }

  function handleCancel() {
    cancelledRef.current = true
    setEditText(text)
    setIsEditing(false)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSave()
    }
    if (e.key === 'Escape') {
      handleCancel()
    }
  }

  const radiusClass = cn(
    'rounded-2xl',
    !isFirst && 'rounded-tr-lg',
    'rounded-br-lg',
  )

  if (isEditing) {
    return (
      <motion.div layout className="flex flex-col items-end gap-1">
        <div className={cn('relative max-w-[300px]', radiusClass, 'border-2 border-primary/30 bg-primary')}>
          <textarea
            ref={textareaRef}
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onBlur={handleSave}
            onKeyDown={handleKeyDown}
            rows={1}
            className="w-full resize-none bg-transparent px-3.5 py-2 text-[13px] leading-relaxed text-primary-foreground outline-none"
            style={{ fieldSizing: 'content' } as React.CSSProperties}
            aria-label={uz.compose.edit}
          />
        </div>
        <div className="text-[10px] text-muted-foreground">
          Enter ↵ {uz.compose.send} · Esc {uz.common.cancel}
        </div>
      </motion.div>
    )
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06, duration: 0.2 }}
      className="group flex items-end justify-end gap-1.5"
    >
      <PencilSimple
        size={12}
        weight="thin"
        className="mb-2 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
        aria-hidden="true"
      />

      <button
        onClick={() => setIsEditing(true)}
        className={cn(
          'max-w-[300px] px-3.5 py-2 text-left text-[13px] leading-relaxed',
          'bg-primary text-primary-foreground',
          'transition-all duration-150 hover:brightness-110 active:scale-[0.98]',
          radiusClass,
        )}
        aria-label={`${uz.compose.edit}: ${text.slice(0, 40)}`}
      >
        {text}
      </button>
    </motion.div>
  )
}
