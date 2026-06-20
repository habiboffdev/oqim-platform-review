import type { ReactElement, ReactNode } from 'react'

export type IconComponent = (props: { className?: string }) => ReactElement

function IconBase({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export const CheckIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="m4.5 12.5 4.5 4.5 10.5-11" />
  </IconBase>
)

export const ProposeIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M12 3.5 13.7 9 19 10.7 13.7 12.4 12 18l-1.7-5.6L5 10.7 10.3 9 12 3.5Z" />
  </IconBase>
)

export const EditIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7.5 18.5 3.5 19.5l1-4Z" />
    <path d="M14.5 5.5l3 3" />
  </IconBase>
)

export const RefreshIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M4 11a8 8 0 0 1 13.7-5.2L20 8" />
    <path d="M20 4v4h-4" />
    <path d="M20 13a8 8 0 0 1-13.7 5.2L4 16" />
    <path d="M4 20v-4h4" />
  </IconBase>
)

export const FileIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M6 3.5h7l5 5V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
    <path d="M13 3.5V8.5h5" />
    <path d="M8.5 13h7M8.5 16.5h5" />
  </IconBase>
)

export const PageIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <rect x="5" y="4" width="14" height="16" rx="1.5" />
    <path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h4" />
  </IconBase>
)

export const RejectIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="m6.5 6.5 11 11M17.5 6.5l-11 11" />
  </IconBase>
)

export const SourceIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M4 6.5C4 5.1 7.6 4 12 4s8 1.1 8 2.5v11C20 18.9 16.4 20 12 20s-8-1.1-8-2.5Z" />
    <path d="M4 6.5C4 7.9 7.6 9 12 9s8-1.1 8-2.5" />
    <path d="M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5" />
  </IconBase>
)

export const SparkleIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M12 3.5 13.7 9 19 10.7 13.7 12.4 12 18l-1.7-5.6L5 10.7 10.3 9 12 3.5Z" />
    <path d="M18.5 4v3M20 5.5h-3" />
  </IconBase>
)

export const MicIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0" />
    <path d="M12 17.5V21M9 21h6" />
  </IconBase>
)

export const StopIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
  </IconBase>
)

export const UploadIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M12 16V4.5M8 8l4-4 4 4" />
    <path d="M5 15v3.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V15" />
  </IconBase>
)
