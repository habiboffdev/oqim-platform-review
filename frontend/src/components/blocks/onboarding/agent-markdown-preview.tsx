import type { ReactNode } from 'react'
import { Circle, PencilSimpleLine, ShieldCheck, Sparkle } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type {
  IngestionProgress,
  OnboardingLearnedReviewItem,
  WorkspaceOSDocumentSectionPreview,
} from '@/lib/types'

// Default Seller skills surfaced inline in the rendered AGENT.md.
// Once `AgentSkill` rows exist in the workspace they will replace this list
// via `render_agent_md(agent, sections, skills)`.
const DEFAULT_SELLER_SKILLS: { slug: string; label: string }[] = [
  { slug: 'catalog-lookup', label: 'Katalogdan mahsulot topish' },
  { slug: 'price-check', label: 'Narx va mavjudlikni tekshirish' },
  { slug: 'order-create', label: 'Buyurtma yozib qoldirish (tasdiqsiz)' },
  { slug: 'followup', label: 'Sovib qolgan mijozni eslatish' },
]

export function AgentMarkdownPreview({
  progress,
  rules,
  documentPreview,
  skillNames,
}: {
  progress: IngestionProgress | undefined
  rules: OnboardingLearnedReviewItem[]
  documentPreview?: WorkspaceOSDocumentSectionPreview[]
  skillNames?: string[]
}) {
  const sections = documentPreview?.slice(0, 4) ?? []
  const skills = skillNames?.length ? skillNames : DEFAULT_SELLER_SKILLS.map((skill) => skill.label)

  return (
    <Card size="sm" className="mt-2 py-0">
      <CardHeader className="px-6 py-5">
        <CardTitle className="font-sans font-semibold tracking-tight">AGENT.md</CardTitle>
        <CardAction>
          <Button type="button" variant="outline" size="sm">
            <PencilSimpleLine />
            Bo‘limni tahrirlash
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-0 px-6 pb-6">
        {sections.length > 0 ? (
          sections.map((section) => (
            <AgentMdSection
              key={section.section_key}
              icon={<Circle className="size-5" />}
              title={section.title}
              body={section.body_preview}
            />
          ))
        ) : (
          <>
            <AgentMdSection
              icon={<Circle className="size-5" />}
              title="Rol"
              body="Sotuv agenti sifatida mijozlarga tez, aniq va do‘stona javob beradi."
            />
            <AgentMdSection
              icon={<Circle className="size-5" />}
              title="Qachon javob beradi"
              body="Mijoz savol beradi, narx so‘raydi, mavjudlikni bilmoqchi bo‘ladi yoki maslahat so‘raydi."
            />
            <AgentMdSection
              icon={<Circle className="size-5" />}
              title="Nimani taxmin qilmaydi"
              body="Narx, mavjudlik, yetkazib berish muddati, chegirmalar va shaxsiy ma’lumotlarni dalilsiz aytmaydi."
            />
          </>
        )}
        <AgentMdSection
          icon={<Sparkle />}
          title="Skills"
          body={
            <ul className="grid gap-1 text-sm leading-6 text-muted-foreground">
              {skills.map((skill) => (
                <li key={skill} className="flex items-baseline gap-2">
                  <span className="font-mono text-[11px] text-foreground/70">·</span>
                  <span className="font-medium text-foreground">{skill}</span>
                </li>
              ))}
            </ul>
          }
        />
        <AgentMdSection
          icon={<ShieldCheck />}
          title="Ruxsat"
          body={rules.length > 0 || progress?.voice_profile_ready
            ? 'Javob yozish, ma’lumot so‘rash va uchrashuv taklif qilish uchun ruxsat bor. Buyurtma tasdiqlash faqat foydalanuvchi tasdig‘idan keyin.'
            : 'Agent ishga tushishidan oldin qoidalar va ovoz yana tekshiriladi.'}
        />
      </CardContent>
    </Card>
  )
}

function AgentMdSection({
  icon,
  title,
  body,
}: {
  icon: ReactNode
  title: string
  body: ReactNode
}) {
  return (
    <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-4 border-b border-border py-4 last:border-b-0">
      <span className="text-muted-foreground [&_svg]:size-5">{icon}</span>
      <span>
        <span className="block font-medium">{title}</span>
        <span className="mt-1 block text-sm leading-6 text-muted-foreground">{body}</span>
      </span>
    </div>
  )
}

export function AgentStatusItem({
  label,
  value,
  variant,
}: {
  label: string
  value: string
  variant: 'success' | 'warning' | 'outline'
}) {
  return (
    <div className="min-w-0">
      <span className="block text-sm font-medium">{label}</span>
      <span
        className={cn(
          'mt-1 block truncate text-sm',
          variant === 'success' && 'text-success',
          variant === 'warning' && 'text-warning',
          variant === 'outline' && 'text-muted-foreground',
        )}
      >
        {value}
      </span>
    </div>
  )
}
