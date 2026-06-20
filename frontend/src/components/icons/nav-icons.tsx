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

export const ChatIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M20 11.4a7.4 7.4 0 0 1-10.6 6.7L4 19.5l1.3-5.2A7.4 7.4 0 1 1 20 11.4Z" />
  </IconBase>
)

export const BrainIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M12 5C10.8 3.6 8.4 4 8 5.8 5.6 5.4 4.4 7.6 5.8 9.2 3.8 10.2 3.8 12.8 5.8 13.4 4.8 15.2 6.6 17.2 8.6 16.4 8.8 18 11 18.2 12 17" />
    <path d="M12 5C13.2 3.6 15.6 4 16 5.8 18.4 5.4 19.6 7.6 18.2 9.2 20.2 10.2 20.2 12.8 18.2 13.4 19.2 15.2 17.4 17.2 15.4 16.4 15.2 18 13 18.2 12 17" />
    <path d="M12 5.2C11.3 7.2 12.7 9.2 12 11.2 11.3 13.2 12.7 15.2 12 16.9" />
  </IconBase>
)

export const DatabaseIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <ellipse cx="12" cy="6" rx="7" ry="3" />
    <path d="M5 6v12c0 1.66 3.13 3 7 3s7-1.34 7-3V6" />
    <path d="M5 12c0 1.66 3.13 3 7 3s7-1.34 7-3" />
  </IconBase>
)

export const RobotIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <rect x="5" y="8" width="14" height="11" rx="2.5" />
    <path d="M12 8V5" />
    <circle cx="12" cy="4" r="1" />
    <path d="M9.5 12.5v1.5M14.5 12.5v1.5" />
    <path d="M3.5 12v3M20.5 12v3" />
  </IconBase>
)

export const SparkIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M12 3.5 13.7 9 19 10.7 13.7 12.4 12 18l-1.7-5.6L5 10.7 10.3 9 12 3.5Z" />
  </IconBase>
)

export const SendIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M20 4 3.5 11l6 2.3 2.3 6L20 4Z" />
    <path d="m9.5 13.3 4-4" />
  </IconBase>
)

export const ChecklistIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M10 6h10M10 12h10M10 18h10" />
    <path d="m4 5.4 1 1 1.6-2M4 11.4l1 1 1.6-2M4 17.4l1 1 1.6-2" />
  </IconBase>
)

export const PlugIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M9 3v5M15 3v5" />
    <path d="M7 8h10v2.5a5 5 0 0 1-10 0V8Z" />
    <path d="M12 15.5V21" />
  </IconBase>
)

export const GearIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="M12 3 14.41 6.18 18.36 5.64 17.82 9.59 21 12 17.82 14.41 18.36 18.36 14.41 17.82 12 21 9.59 17.82 5.64 18.36 6.18 14.41 3 12 6.18 9.59 5.64 5.64 9.59 6.18Z" />
    <circle cx="12" cy="12" r="3" />
  </IconBase>
)

export const CaretDownIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <path d="m6 9 6 6 6-6" />
  </IconBase>
)

export const SearchIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.6-3.6" />
  </IconBase>
)

export const KanbanIcon: IconComponent = ({ className }) => (
  <IconBase className={className}>
    <rect x="3" y="3" width="5" height="18" rx="1.5" />
    <rect x="10" y="3" width="5" height="12" rx="1.5" />
    <rect x="17" y="3" width="5" height="15" rx="1.5" />
  </IconBase>
)
