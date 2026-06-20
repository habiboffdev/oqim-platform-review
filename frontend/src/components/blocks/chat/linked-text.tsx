import type { MessageTextEntity } from '@/lib/types'

type TextSegment =
  | { type: 'text'; value: string }
  | { type: 'link'; value: string; href: string }
  | { type: 'custom_emoji'; documentId: string; alt: string }

function parseTextSegments(text: string): TextSegment[] {
  // Match markdown links [text](url) OR bare URLs
  const regex = /\[([^\]]*?\*{0,2}[^\]]*?)\]\((https?:\/\/[^)]+)\)|\*{2}([^*]+)\*{2}|(https?:\/\/[^\s<>"{}|\\^`[\]]+)/g
  const segments: TextSegment[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', value: text.slice(lastIndex, match.index) })
    }

    if (match[1] !== undefined && match[2]) {
      // Markdown link [text](url) — strip ** from link text
      const linkText = match[1].replace(/\*{2}/g, '')
      segments.push({ type: 'link', value: linkText, href: match[2] })
    } else if (match[3] !== undefined) {
      // Bold **text** — render as plain text (strip **)
      segments.push({ type: 'text', value: match[3] })
    } else if (match[4]) {
      // Bare URL
      segments.push({ type: 'link', value: match[4], href: match[4] })
    }
    lastIndex = regex.lastIndex
  }

  if (lastIndex < text.length) {
    segments.push({ type: 'text', value: text.slice(lastIndex) })
  }

  return segments
}

interface LinkedTextProps {
  text: string
  textEntities?: MessageTextEntity[]
}

function appendParsedText(target: TextSegment[], text: string) {
  if (!text) return
  target.push(...parseTextSegments(text))
}

function normalizeCustomEmojiEntities(textEntities?: MessageTextEntity[]): MessageTextEntity[] {
  if (!Array.isArray(textEntities)) return []
  return textEntities
    .filter((entity) =>
      entity?.type === 'custom_emoji'
      && typeof entity.offset === 'number'
      && typeof entity.length === 'number'
      && entity.length > 0
      && typeof entity.document_id === 'string'
      && entity.document_id.length > 0,
    )
    .sort((left, right) => left.offset - right.offset)
}

function buildSegments(text: string, textEntities?: MessageTextEntity[]): TextSegment[] {
  const entities = normalizeCustomEmojiEntities(textEntities)
  if (!entities.length) return parseTextSegments(text)

  const segments: TextSegment[] = []
  let cursor = 0

  for (const entity of entities) {
    const start = Math.max(0, Math.min(entity.offset, text.length))
    const end = Math.max(start, Math.min(entity.offset + entity.length, text.length))
    if (start < cursor) continue
    appendParsedText(segments, text.slice(cursor, start))
    segments.push({
      type: 'custom_emoji',
      documentId: entity.document_id!,
      alt: text.slice(start, end) || 'emoji',
    })
    cursor = end
  }

  appendParsedText(segments, text.slice(cursor))
  return segments
}

function buildCustomEmojiUrl(documentId: string): string {
  return `/api/media/custom-emoji/${encodeURIComponent(documentId)}`
}

export function LinkedText({ text, textEntities }: LinkedTextProps) {
  const segments = buildSegments(text, textEntities)

  if (segments.length === 1 && segments[0].type === 'text') {
    return <>{text}</>
  }

  return (
    <>
      {segments.map((seg, i) =>
        seg.type === 'text' ? (
          <span key={i}>{seg.value}</span>
        ) : seg.type === 'custom_emoji' ? (
          <img
            key={i}
            src={buildCustomEmojiUrl(seg.documentId)}
            alt={seg.alt}
            className="tg-inline-custom-emoji"
            draggable={false}
          />
        ) : (
          <a
            key={i}
            href={seg.href}
            target="_blank"
            rel="noopener noreferrer"
            className="tg-link"
            onClick={(e) => e.stopPropagation()}
          >
            {seg.value}
          </a>
        ),
      )}
    </>
  )
}
