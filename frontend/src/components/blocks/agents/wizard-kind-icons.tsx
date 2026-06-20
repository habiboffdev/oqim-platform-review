import type { ReactElement, ReactNode } from "react"

export type WizardKind = "seller" | "support" | "follow_up" | "custom"
type IconProps = { className?: string }

function IconBase({ className, children }: { className?: string; children: ReactNode }): ReactElement {
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

const SellerIcon = ({ className }: IconProps) => (
  <IconBase className={className}>
    <path d="M4 7h16l-1.2 9.2A2 2 0 0 1 16.8 18H7.2a2 2 0 0 1-2-1.8L4 7Z" />
    <path d="M8.5 7V5.5a3.5 3.5 0 0 1 7 0V7" />
  </IconBase>
)

const SupportIcon = ({ className }: IconProps) => (
  <IconBase className={className}>
    <path d="M5 12a7 7 0 0 1 14 0v4a2 2 0 0 1-2 2h-1v-6h3" />
    <path d="M5 12H2v4a2 2 0 0 0 2 2h1v-6" />
  </IconBase>
)

const FollowUpIcon = ({ className }: IconProps) => (
  <IconBase className={className}>
    <path d="M3 12a9 9 0 1 0 3-6.7" />
    <path d="M3 4v4h4" />
    <path d="M12 8v4l3 2" />
  </IconBase>
)

const CustomIcon = ({ className }: IconProps) => (
  <IconBase className={className}>
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
    <circle cx="12" cy="12" r="3.2" />
  </IconBase>
)

export const WIZARD_KIND_ICONS: Record<WizardKind, (props: IconProps) => ReactElement> = {
  seller: SellerIcon,
  support: SupportIcon,
  follow_up: FollowUpIcon,
  custom: CustomIcon,
}
