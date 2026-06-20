import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { uz } from '@/lib/uz'
import { SourceIcon } from '@/components/icons/doc-icons'
import {
  useGenerateOnboardingDocuments,
  useOnboardingDocuments,
  useOnboardingDocumentsStream,
} from '@/hooks/use-onboarding-documents'
import { useOnboardingRuntime } from '@/hooks/use-onboarding-runtime'
import { useAgents } from '@/hooks/use-agents'
import {
  useApproveSkillCandidate,
  useRejectSkillCandidate,
  useSkillCandidates,
} from '@/hooks/use-skill-candidates'
import { useMountEffect } from '@/hooks/use-mount-effect'
import { queryKeys } from '@/lib/query-keys'
import { api } from '@/lib/api-client'
import { DocumentToggle, type DocumentTab } from './document-toggle'
import {
  DocumentSectionCard,
  type SectionDisplayStatus,
} from './document-section-card'
import { AgentMdPaths } from './agent-md-paths'
import { SkillCandidateCard } from './skill-candidate-card'
import { SourceMirrorRail } from './source-mirror-rail'
import type {
  OnboardingDocumentBlock,
  OnboardingDocumentSection,
  OnboardingDocumentsProjection,
  OnboardingSkillStatus,
  SkillCandidate,
} from '@/lib/types'

interface PhaseDocumentsProps {
  enabled?: boolean
  onNext: () => void
}

type SectionOverride = 'approved' | 'skipped'

export function PhaseDocuments({ enabled = true, onNext }: PhaseDocumentsProps) {
  const queryClient = useQueryClient()
  const { data: projection, isLoading } = useOnboardingDocuments(enabled)
  useOnboardingDocumentsStream(enabled)
  const generate = useGenerateOnboardingDocuments()
  const runtime = useOnboardingRuntime(enabled)
  const { data: agents } = useAgents()
  const skillCandidates = useSkillCandidates(enabled)

  // The onboarding workbench shapes the primary Seller agent's AGENT.md. Agents
  // are bootstrapped by the document generate flow, so this is undefined until
  // the first launch lands; `AgentMdPaths` renders a calm empty state meanwhile.
  // `useAgents` orders is_default first, so the default (Seller) agent leads.
  const primaryAgentId = useMemo(() => {
    if (!agents?.length) return undefined
    const seller =
      agents.find((agent) => agent.agent_type === 'customer') ??
      agents.find((agent) => agent.is_default) ??
      agents[0]
    return seller?.id
  }, [agents])

  const [activeTab, setActiveTab] = useState<DocumentTab>('business')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<string, SectionOverride>>({})

  // Kick off whole-document generation once when the phase mounts, but only if
  // nothing is running and nothing has landed yet. Reads a fresh snapshot so
  // the decision does not depend on render timing; runs exactly once on mount.
  useMountEffect(() => {
    if (!enabled) return
    let cancelled = false
    void (async () => {
      try {
        const snapshot = await queryClient.ensureQueryData<OnboardingDocumentsProjection>({
          queryKey: queryKeys.onboardingDocuments,
          queryFn: () =>
            api.get<OnboardingDocumentsProjection>('/api/onboarding/documents'),
        })
        if (cancelled) return
        const { business, agent } = snapshot.documents
        const landed =
          business.approved + business.proposed + agent.approved + agent.proposed
        if (!snapshot.running && landed === 0) {
          generate.mutate()
        }
      } catch {
        // Snapshot unavailable — the visible empty state covers this; the owner
        // can still proceed and generation retries on the next mount.
      }
    })()
    return () => {
      cancelled = true
    }
  })

  const block: OnboardingDocumentBlock | undefined =
    activeTab === 'skill' ? undefined : projection?.documents[activeTab]

  const overrideKey = (key: string) => `${activeTab}:${key}`

  const displayStatusFor = (section: OnboardingDocumentSection): SectionDisplayStatus => {
    const override = overrides[overrideKey(section.key)]
    if (override) return override
    return section.status
  }

  const selectedSection = useMemo<OnboardingDocumentSection | null>(() => {
    if (!block || !selectedKey) return null
    return block.sections.find((section) => section.key === selectedKey) ?? null
  }, [block, selectedKey])

  const setOverride = (key: string, value: SectionOverride) => {
    setOverrides((current) => ({ ...current, [overrideKey(key)]: value }))
  }

  const rail = (
    <SourceMirrorRail
      sourceLearning={runtime.data?.source_learning}
      selectedSection={selectedSection}
      onRetry={runtime.refetch}
    />
  )

  return (
    <section className="flex min-h-0 w-full flex-col gap-3 lg:h-[calc(100dvh-7.25rem)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border">
        <DocumentToggle
          active={activeTab}
          onChange={setActiveTab}
          projection={projection}
          skillCount={skillCandidates.data?.length}
        />
        <div className="flex items-center gap-2 pb-2">
          <Sheet>
            <SheetTrigger
              render={
                <Button type="button" variant="outline" size="sm" className="lg:hidden">
                  <SourceIcon className="size-4" />
                  {uz.onboarding.documents.mobileSourcesTrigger}
                </Button>
              }
            />
            <SheetContent side="right" className="w-[88vw] max-w-sm p-4">
              <SheetHeader className="px-0">
                <SheetTitle>{uz.onboarding.documents.mobileSourcesTitle}</SheetTitle>
              </SheetHeader>
              {rail}
            </SheetContent>
          </Sheet>
          <Button type="button" size="sm" onClick={onNext}>
            {uz.onboarding.documents.continue}
          </Button>
        </div>
      </header>

      {projection?.error ? (
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
          {uz.onboarding.documents.streamError}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
        <div className="min-h-0 overflow-y-auto pr-1">
          {activeTab === 'skill' ? (
            <SkillPanel
              status={projection?.documents.skill.status ?? 'pending'}
              candidates={skillCandidates.data}
              isLoading={skillCandidates.isLoading}
              isError={skillCandidates.isError}
            />
          ) : isLoading && !projection ? (
            <DocumentSkeletonList />
          ) : block ? (
            <div className="grid gap-2.5">
              {activeTab === 'agent' ? (
                <AgentMdPaths
                  agentId={primaryAgentId}
                  alreadyGenerated={block.approved + block.proposed > 0}
                />
              ) : null}
              {block.sections.map((section) => (
                <DocumentSectionCard
                  key={section.key}
                  section={section}
                  displayStatus={displayStatusFor(section)}
                  selected={selectedKey === section.key}
                  onSelect={() => setSelectedKey(section.key)}
                  onAccept={() => setOverride(section.key, 'approved')}
                  onReject={() => setOverride(section.key, 'skipped')}
                  onEdit={() => setSelectedKey(section.key)}
                />
              ))}
            </div>
          ) : (
            <DocumentSkeletonList />
          )}
        </div>
        <div className="hidden min-h-0 lg:block">{rail}</div>
      </div>
    </section>
  )
}

function DocumentSkeletonList() {
  return (
    <div className="grid gap-2.5">
      {[0, 1, 2].map((index) => (
        <div key={index} className="rounded-lg border border-border bg-card px-4 py-3.5">
          <Skeleton className="h-4 w-40" />
          <div className="mt-3 grid gap-2">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-[72%]" />
          </div>
        </div>
      ))}
    </div>
  )
}

function SkillPanel({
  status,
  candidates,
  isLoading,
  isError,
}: {
  status: OnboardingSkillStatus
  candidates: SkillCandidate[] | undefined
  isLoading: boolean
  isError: boolean
}) {
  const approve = useApproveSkillCandidate()
  const reject = useRejectSkillCandidate()
  // Flips once the owner reviews any candidate, so an emptied list resolves to the
  // "all reviewed" state even when the projection status is stale (Redis never
  // flips to "proposed" for skills learned/seeded outside docgen, or can lag the DB).
  const [reviewedAny, setReviewedAny] = useState(false)

  // Live DB candidates win over the projection's Redis status snapshot, which
  // never updates on review.
  if (candidates && candidates.length > 0) {
    return (
      <div className="grid gap-2.5">
        <div>
          <p className="text-sm font-semibold text-foreground">
            {uz.onboarding.documents.skillProposedTitle}
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {uz.onboarding.documents.skillProposedSubtitle}
          </p>
        </div>
        {candidates.map((candidate) => (
          <SkillCandidateCard
            key={candidate.id}
            candidate={candidate}
            onApprove={() => {
              setReviewedAny(true)
              approve.mutate(candidate.id)
            }}
            onReject={() => {
              setReviewedAny(true)
              reject.mutate(candidate.id)
            }}
          />
        ))}
      </div>
    )
  }

  if (candidates === undefined && isLoading) {
    return <DocumentSkeletonList />
  }

  if (candidates === undefined && isError) {
    return (
      <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
        {uz.onboarding.documents.streamError}
      </div>
    )
  }

  const copy = (() => {
    if (status === 'degraded') {
      return {
        title: uz.onboarding.documents.skillDegradedTitle,
        body: uz.onboarding.documents.skillDegradedBody,
      }
    }
    if (status === 'learning') {
      return {
        title: uz.onboarding.documents.skillLearningTitle,
        body: uz.onboarding.documents.skillLearningBody,
      }
    }
    if (status === 'proposed' || reviewedAny) {
      // No live candidates left, but learning produced some (proposed) or we just
      // reviewed the last one → everything has been reviewed.
      return {
        title: uz.onboarding.documents.skillReviewedTitle,
        body: uz.onboarding.documents.skillReviewedBody,
      }
    }
    return {
      title: uz.onboarding.documents.skillPendingTitle,
      body: uz.onboarding.documents.skillPendingBody,
    }
  })()

  return (
    <div className="grid place-items-center rounded-lg border border-dashed border-border px-6 py-16 text-center">
      <div className="max-w-sm">
        <p className="text-sm font-semibold text-foreground">{copy.title}</p>
        <p className="mt-1.5 text-sm text-muted-foreground">{copy.body}</p>
      </div>
    </div>
  )
}
