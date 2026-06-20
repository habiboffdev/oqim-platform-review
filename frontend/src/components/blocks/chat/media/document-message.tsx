import {
  File,
  FilePdf,
  FileDoc,
  FileXls,
  FileZip,
  FileImage,
  FileText,
} from '@phosphor-icons/react'
import { uz } from '@/lib/uz'
import type { Message } from '@/lib/types'
import { getFullMediaUrl } from './urls'

interface DocumentMeta {
  file_name?: string
  file_size?: number
  mime_type?: string
}

interface DocumentMessageProps {
  message: Message
}

function getFileIcon(mimeType?: string) {
  if (!mimeType) return File
  if (mimeType.includes('pdf')) return FilePdf
  if (mimeType.includes('word') || mimeType.includes('doc')) return FileDoc
  if (mimeType.includes('sheet') || mimeType.includes('xls')) return FileXls
  if (mimeType.includes('zip') || mimeType.includes('rar') || mimeType.includes('7z'))
    return FileZip
  if (mimeType.startsWith('image/')) return FileImage
  if (mimeType.startsWith('text/')) return FileText
  return File
}

function formatFileSize(bytes?: number): string {
  if (!bytes || bytes === 0) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / 1024 ** i).toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

export function DocumentMessage({ message }: DocumentMessageProps) {
  const meta = message.media_metadata as DocumentMeta | undefined
  const FileIcon = getFileIcon(meta?.mime_type)
  const fileSize = formatFileSize(meta?.file_size)
  const fullUrl = getFullMediaUrl(message)

  return (
    <div
      className="tg-document-row"
      onClick={() => fullUrl && window.open(fullUrl, '_blank')}
      style={{ cursor: fullUrl ? 'pointer' : 'default' }}
      role={fullUrl ? 'link' : undefined}
      aria-label={meta?.file_name || uz.conversations.document}
    >
      <div className="tg-document-icon">
        <FileIcon size={20} weight="thin" />
      </div>
      <div className="tg-document-info">
        <span className="tg-document-name">{meta?.file_name || uz.conversations.document}</span>
        {fileSize && <span className="tg-document-size">{fileSize}</span>}
      </div>
    </div>
  )
}
