import { type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Minimal, dependency-free markdown renderer for LLM-generated document bodies
 * (BUSINESS.md / AGENT.md sections): paragraphs, `##`/`###` headings, `-`/`*`
 * bullet and `1.` ordered lists, plus inline `**bold**`, `*italic*`, `` `code` ``.
 *
 * Builds JSX nodes directly rather than injecting raw HTML, so it is XSS-safe
 * by construction. Swap for `react-markdown` if section bodies ever need
 * tables, links, or nested lists — for now LLM section prose stays in-scope.
 */

interface MarkdownProps {
  content: string
  className?: string
}

const _HEADING = /^(#{1,6})\s+(.*)$/
const _BULLET = /^\s*[-*]\s+(.*)$/
const _ORDERED = /^\s*\d+\.\s+(.*)$/
const _INLINE = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`)/g

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let lastIndex = 0
  let i = 0
  for (const match of text.matchAll(_INLINE)) {
    const index = match.index ?? 0
    if (index > lastIndex) nodes.push(text.slice(lastIndex, index))
    const key = `${keyPrefix}-i${i++}`
    if (match[2] !== undefined) {
      nodes.push(<strong key={key} className="font-semibold text-foreground">{match[2]}</strong>)
    } else if (match[3] !== undefined) {
      nodes.push(<em key={key}>{match[3]}</em>)
    } else if (match[4] !== undefined) {
      nodes.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 text-[0.85em]">{match[4]}</code>,
      )
    }
    lastIndex = index + match[0].length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

type Block =
  | { kind: 'heading'; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'p'; lines: string[] }

function parseBlocks(content: string): Block[] {
  const blocks: Block[] = []
  let para: string[] = []
  const flushParagraph = () => {
    if (para.length > 0) {
      blocks.push({ kind: 'p', lines: para })
      para = []
    }
  }
  for (const rawLine of content.replace(/\r\n/g, '\n').split('\n')) {
    const line = rawLine.trimEnd()
    if (line.trim() === '') {
      flushParagraph()
      continue
    }
    const heading = line.match(_HEADING)
    if (heading) {
      flushParagraph()
      blocks.push({ kind: 'heading', text: heading[2] })
      continue
    }
    const bullet = line.match(_BULLET)
    if (bullet) {
      flushParagraph()
      const last = blocks[blocks.length - 1]
      if (last && last.kind === 'ul') last.items.push(bullet[1])
      else blocks.push({ kind: 'ul', items: [bullet[1]] })
      continue
    }
    const ordered = line.match(_ORDERED)
    if (ordered) {
      flushParagraph()
      const last = blocks[blocks.length - 1]
      if (last && last.kind === 'ol') last.items.push(ordered[1])
      else blocks.push({ kind: 'ol', items: [ordered[1]] })
      continue
    }
    para.push(line)
  }
  flushParagraph()
  return blocks
}

export function Markdown({ content, className }: MarkdownProps) {
  const blocks = parseBlocks(content ?? '')
  if (blocks.length === 0) return null
  return (
    <div className={cn('grid gap-2 text-sm leading-relaxed text-foreground/80', className)}>
      {blocks.map((block, index) => {
        const key = `b${index}`
        if (block.kind === 'heading') {
          return (
            <p key={key} className="text-sm font-semibold text-foreground">
              {renderInline(block.text, key)}
            </p>
          )
        }
        if (block.kind === 'ul') {
          return (
            <ul key={key} className="grid list-disc gap-1 pl-5">
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ul>
          )
        }
        if (block.kind === 'ol') {
          return (
            <ol key={key} className="grid list-decimal gap-1 pl-5">
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ol>
          )
        }
        return <p key={key}>{renderInline(block.lines.join(' '), key)}</p>
      })}
    </div>
  )
}
