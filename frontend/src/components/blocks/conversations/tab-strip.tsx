import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'

export type ConversationTab = 'chats' | 'replies' | 'pipeline'

interface TabStripProps {
  activeTab: ConversationTab
  onTabChange: (tab: ConversationTab) => void
  replyCount: number
}

const tabs: { id: ConversationTab; label: string }[] = [
  { id: 'chats', label: uz.conversations.tabs.chats },
  { id: 'replies', label: uz.conversations.tabs.replies },
  { id: 'pipeline', label: uz.conversations.tabs.pipeline },
]

export function TabStrip({ activeTab, onTabChange, replyCount }: TabStripProps) {
  return (
    <div role="tablist" aria-label={uz.conversations.tabsLabel} className="flex gap-0.5 rounded-lg bg-muted/50 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          id={`tab-${tab.id}`}
          role="tab"
          aria-selected={activeTab === tab.id}
          tabIndex={activeTab === tab.id ? 0 : -1}
          onClick={() => onTabChange(tab.id)}
          className={cn(
            'relative flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
            activeTab === tab.id
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {tab.label}
          {tab.id === 'replies' && replyCount > 0 && (
            <motion.span
              key={replyCount}
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              className="flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-background"
            >
              {replyCount}
            </motion.span>
          )}
        </button>
      ))}
    </div>
  )
}
