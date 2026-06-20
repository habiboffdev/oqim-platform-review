import type {
  SellerAgentReply,
  BusinessBrainFactReadModel,
  CatalogWorkspaceProduct,
  CommercialActionProposal,
  KnowledgeItem,
  OnboardingSourceLearningSource,
} from '@/lib/types'
import { readyCatalogProducts, uniqueCatalogSources } from './catalog-workbench-model'

export type BrainSurface = 'sources' | 'catalog' | 'knowledge' | 'rules' | 'voice' | 'pairs' | 'company'

export interface BrainStats {
  sources: number
  readyGrounding: number
  reviewQueue: number
  learningPairs: number
}

export interface BrainSourceSummary {
  fact: BusinessBrainFactReadModel
  title: string
  kind: string
  status: string
  outputs: Array<{ label: string; count: number }>
  sourceUnits: number
  media: number
  preview: string
  sourceRef: string
  degradedReasons: string[]
  retryable: boolean
}

export function brainStats({
  facts,
  products,
  knowledge,
  replies,
  proposals,
}: {
  facts: BusinessBrainFactReadModel[]
  products: CatalogWorkspaceProduct[]
  knowledge: KnowledgeItem[]
  replies: SellerAgentReply[]
  proposals: CommercialActionProposal[]
}): BrainStats {
  return {
    sources: sourceFacts(facts).length,
    readyGrounding: readyCatalogProducts(products)
      + knowledge.filter((item) => item.confirmed).length
      + facts.filter((fact) => surfaceForFact(fact) === 'rules' && fact.status === 'active').length,
    reviewQueue: proposals.filter((item) => item.lifecycle_state === 'waiting_approval' || item.lifecycle_state === 'proposed').length
      + facts.filter((fact) => fact.status === 'proposed' || fact.status === 'degraded').length,
    learningPairs: facts.filter((fact) => surfaceForFact(fact) === 'pairs').length
      + replies.filter((reply) => reply.learning_runtime?.state === 'learned').length,
  }
}

export function sourceFacts(facts: BusinessBrainFactReadModel[]) {
  return facts.filter((fact) => (
    (fact.fact_type === 'business_source_media_fact'
      || (fact.fact_type === 'business_source_fact' && fact.entity_ref.startsWith('workspace:source:')))
    && !isInternalAggregateRef(fact.entity_ref)
  ))
}

export function surfaceForFact(fact: BusinessBrainFactReadModel): BrainSurface {
  if (
    fact.fact_type === 'business_source_media_fact'
    || (fact.fact_type === 'business_source_fact' && fact.entity_ref.startsWith('workspace:source:'))
  ) return 'sources'
  if (fact.fact_type.startsWith('catalog_')) return 'catalog'
  if (fact.fact_type === 'knowledge_fact') return 'knowledge'
  if (fact.fact_type.includes('rule') || fact.fact_type.includes('policy')) return 'rules'
  if (fact.fact_type === 'voice_fact') return 'voice'
  if (fact.fact_type === 'conversation_pair_fact' || fact.fact_type === 'correction_episode_fact') return 'pairs'
  return 'company'
}

export function sourceSummaries(
  facts: BusinessBrainFactReadModel[],
  learningSources: OnboardingSourceLearningSource[] = [],
): BrainSourceSummary[] {
  const learningByRef = new Map(learningSources.map((source) => [source.source_ref, source]))
  const summaries = sourceFacts(facts).map((fact) => {
    const processing = objectValue(fact.value.processing)
    const metadata = objectValue(fact.value.metadata)
    const input = objectValue(fact.value.input)
    const sourceRef = sourceRefForFact(fact)
    const learning = learningByRef.get(sourceRef)
    const learnedTitle = stringValue(learning?.label)
    const title = learnedTitle && !isInternalRef(learnedTitle) ? learnedTitle : factTitle(fact)
    return {
      fact,
      title,
      kind: kindLabel(
        stringValue(input.content_type)
        || stringValue(input.file_name)
        || stringValue(fact.value.kind)
        || stringValue(metadata.content_type)
        || fact.fact_type,
      ),
      status: learning?.status || stringValue(processing.state) || fact.status,
      outputs: sourceOutputs(facts, sourceRef, fact.fact_id),
      sourceUnits: learning?.source_unit_count ?? numberValue(processing.source_unit_count),
      media: learning?.source_media_count ?? numberValue(processing.source_media_count),
      preview: cleanPreview(stringValue(fact.value.text_preview) || factSummary(fact)),
      sourceRef,
      degradedReasons: learning?.degraded_reasons ?? degradedReasonsForFact(fact, processing),
      retryable: learning?.retryable ?? false,
    }
  })
  const byRef = new Map<string, BrainSourceSummary>()
  for (const summary of summaries) {
    const existing = byRef.get(summary.sourceRef)
    if (!existing || sourceSummaryRank(summary) >= sourceSummaryRank(existing)) {
      byRef.set(summary.sourceRef, {
        ...summary,
        sourceUnits: Math.max(summary.sourceUnits, existing?.sourceUnits ?? 0),
        media: Math.max(summary.media, existing?.media ?? 0),
        preview: summary.preview || existing?.preview || '',
        outputs: mergeSourceOutputs(existing?.outputs ?? [], summary.outputs),
        degradedReasons: Array.from(new Set([...(existing?.degradedReasons ?? []), ...summary.degradedReasons])),
        retryable: summary.retryable || Boolean(existing?.retryable),
      })
    }
  }
  for (const source of learningSources) {
    if (byRef.has(source.source_ref)) continue
    byRef.set(source.source_ref, {
      fact: learningSourceFact(source),
      title: stringValue(source.label) && !isInternalRef(source.label) ? source.label : kindLabel(source.kind),
      kind: kindLabel(source.kind),
      status: source.status,
      outputs: sourceOutputs(facts, source.source_ref, source.fact_id),
      sourceUnits: source.source_unit_count,
      media: source.source_media_count,
      preview: '',
      sourceRef: source.source_ref,
      degradedReasons: source.degraded_reasons,
      retryable: source.retryable,
    })
  }
  return Array.from(byRef.values())
}

export function factsForSurface(facts: BusinessBrainFactReadModel[], surface: BrainSurface) {
  return facts.filter((fact) => surfaceForFact(fact) === surface)
}

export function brainKnowledgeItems(facts: BusinessBrainFactReadModel[]): KnowledgeItem[] {
  return facts
    .filter((fact) => fact.fact_type === 'knowledge_fact' && ['active', 'confirmed'].includes(fact.status))
    .map((fact, index) => {
      const category = stringValue(fact.value.category) || stringValue(fact.value.topic) || 'general'
      return factToKnowledgeItem(fact, index, category)
    })
    .filter((item) => item.title.length > 0 || item.content.length > 0)
}

export function brainRuleItems(facts: BusinessBrainFactReadModel[]): KnowledgeItem[] {
  return facts
    .filter((fact) => fact.fact_type === 'seller_rule_fact' && ['active', 'confirmed'].includes(fact.status))
    .map((fact, index) => factToKnowledgeItem(fact, index, 'rule'))
    .filter((item) => item.content.length > 0)
}

export function factTitle(fact: BusinessBrainFactReadModel) {
  const value = fact.value
  if (fact.fact_type === 'conversation_pair_fact') return pairTitle(fact)
  if (fact.fact_type === 'correction_episode_fact') return 'Tuzatishdan o‘rganilgan javob'
  const input = objectValue(value.input)
  const metadata = objectValue(value.metadata)
  const state = objectValue(value.state)
  const explicitTitle = stringValue(value.title)
    || stringValue(value.name)
    || stringValue(value.label)
    || stringValue(value.question)
    || stringValue(value.topic)
    || stringValue(value.rule)
    || stringValue(input.label)
    || stringValue(input.file_name)
    || stringValue(input.url)
    || stringValue(input.handle)
    || stringValue(metadata.file_name)
  if (explicitTitle && !isInternalRef(explicitTitle)) return explicitTitle
  const stateTitle = stateFactTitle(fact, state)
  if (stateTitle) return stateTitle
  if (fact.fact_type === 'business_source_fact') return `${kindLabel(stringValue(value.kind) || stringValue(input.kind) || 'source')} manbasi`
  if (fact.fact_type === 'business_source_media_fact') return 'Media manbasi'
  if (fact.fact_type === 'voice_fact') return 'Sotuvchi uslubi'
  return 'Ma’lumot'
}

export function factSummary(fact: BusinessBrainFactReadModel) {
  const value = fact.value
  const state = objectValue(value.state)
  const observations = Array.isArray(value.observations)
    ? value.observations.filter((item): item is string => typeof item === 'string')
    : []
  return cleanPreview(stringValue(value.answer)
    || stringValue(value.content)
    || stringValue(value.description)
    || stringValue(value.summary)
    || stringValue(value.requirement)
    || stringValue(value.rule)
    || stringValue(value.instruction)
    || observations.join(' ')
    || stringValue(value.text)
    || stringValue(value.text_preview)
    || pairSummary(fact)
    || (surfaceForFact(fact) === 'sources' ? '' : stateFactSummary(fact, state))
    || '')
}

export function editableFactText(fact: BusinessBrainFactReadModel) {
  const value = fact.value
  if (fact.fact_type === 'conversation_pair_fact') return conversationPairParts(fact).seller
  if (fact.fact_type === 'correction_episode_fact') {
    return stringValue(value.final_output) || stringValue(value.human_feedback)
  }
  return factSummary(fact)
}

export function conversationPairParts(fact: BusinessBrainFactReadModel) {
  const value = fact.value
  const summary = stringValue(value.summary)
  const customer = firstNonEmpty(
    stringValue(value.customer_turn),
    extractLabeledLine(summary, ['Mijoz', 'Customer']),
  )
  const rawSeller = firstNonEmpty(
    stringValue(value.seller_turn),
    extractLabeledLine(summary, ['Sotuvchi', 'Seller']),
    stringValue(value.final_output),
  )
  const contextBefore = Array.isArray(value.context_before)
    ? value.context_before.map((item) => stringValue(item)).filter(Boolean)
    : extractLabeledList(summary, ['Oldingi kontekst', 'Previous context'])
  return {
    customer: cleanLabeledTranscript(customer, 'customer'),
    seller: cleanLabeledTranscript(rawSeller, 'seller'),
    contextBefore,
  }
}

export function factOwnerLabel(fact: BusinessBrainFactReadModel) {
  const surface = surfaceForFact(fact)
  if (surface === 'catalog') return 'Katalog'
  if (surface === 'knowledge') return 'Bilim bazasi'
  if (surface === 'rules') return 'Qoidalar'
  if (surface === 'voice') return 'Ovoz'
  if (surface === 'pairs') return 'Suhbatdan o‘rganish'
  if (surface === 'sources') return 'Manbalar'
  return 'Kompaniya'
}

export function factPrimarySourceLabel(fact: BusinessBrainFactReadModel) {
  const visible = fact.source_refs.find((ref) => !isInternalAggregateRef(ref))
  return visible ? compactRef(visible) : 'Dalil yo‘q'
}

export function visibleSourceLabels(fact: BusinessBrainFactReadModel, limit = 3) {
  const labels = fact.source_refs
    .filter((ref) => !isInternalAggregateRef(ref))
    .map(compactRef)
    .filter((label) => label && !isInternalRef(label))
  return Array.from(new Set(labels)).slice(0, limit)
}

export function factRepairActionLabel(fact: BusinessBrainFactReadModel, mergeOptions: BusinessBrainFactReadModel[] = []) {
  if (fact.status === 'conflict') return mergeOptions.length ? 'Konfliktni birlashtirish' : 'Konfliktni tuzatish'
  if (fact.source_refs.length === 0) return 'Dalil qo‘shish'
  if (!factSummary(fact)) return 'Matnni to‘ldirish'
  if (fact.status === 'degraded') return 'Qayta tekshirish'
  if (fact.status === 'proposed') return 'Tasdiqlash'
  return 'Tahrirlash'
}

function isInternalRef(value: string) {
  return /^(message|conversation|onboarding|brain|workspace):/i.test(value.trim())
}

function factToKnowledgeItem(fact: BusinessBrainFactReadModel, index: number, category: string): KnowledgeItem {
  return {
    id: -(index + 1),
    workspace_id: fact.workspace_id,
    title: factTitle(fact),
    content: factSummary(fact),
    category,
    source: 'business_brain',
    ai_confidence: fact.confidence,
    confirmed: true,
    frequency: null,
    is_active: ['active', 'confirmed'].includes(fact.status),
    created_at: fact.valid_from,
    updated_at: fact.valid_until ?? fact.valid_from,
  }
}

export function kindLabel(value: string) {
  if (value.includes('website') || value.includes('html')) return 'Sayt'
  if (value.includes('telegram')) return 'Telegram'
  if (value.includes('pdf')) return 'PDF'
  if (value.includes('spreadsheet') || value.includes('csv') || value.includes('excel')) return 'Jadval'
  if (value.includes('voice') || value.includes('audio')) return 'Ovoz'
  if (value.includes('screenshot') || value.includes('image')) return 'Rasm'
  if (value.includes('text')) return 'Matn'
  return 'Manba'
}

export function compactRef(value: string) {
  const normalized = value.trim()
  if (/^message:\d+$/i.test(normalized)) return 'Suhbat dalili'
  if (/^conversation:/i.test(normalized)) return 'Suhbat tarixi'
  if (/^onboarding:source:/i.test(normalized)) return 'Onboarding manbasi'
  if (/^brain:source:/i.test(normalized)) return 'O‘qilgan manba'
  if (/^workspace:source:/i.test(normalized)) return 'Manba'
  if (/^business_source:/i.test(normalized)) return 'O‘qilgan manba'
  return value.split(':').slice(-2).join(':') || value
}

export function sourceRefForFact(fact: BusinessBrainFactReadModel) {
  const sourceRef = fact.source_refs.find((ref) => ref.startsWith('onboarding:source:') || ref.startsWith('brain:source:'))
  if (sourceRef) return sourceRef
  if (fact.entity_ref.startsWith('workspace:source:')) {
    return fact.entity_ref.replace('workspace:source:', '')
  }
  return fact.fact_id
}

export function brainReadinessFacts({
  facts,
  products,
  knowledge,
}: {
  facts: BusinessBrainFactReadModel[]
  products: CatalogWorkspaceProduct[]
  knowledge: KnowledgeItem[]
}) {
  const rules = factsForSurface(facts, 'rules')
  const voice = factsForSurface(facts, 'voice')
  const pairs = factsForSurface(facts, 'pairs')
  return [
    {
      label: 'Katalog',
      value: `${readyCatalogProducts(products)} / ${products.length}`,
      helper: `${uniqueCatalogSources(products).length} manba`,
      ready: readyCatalogProducts(products) > 0,
    },
    {
      label: 'Bilim va FAQ',
      value: `${knowledge.filter((item) => item.confirmed).length} / ${knowledge.length}`,
      helper: 'mijoz savollariga javob',
      ready: knowledge.some((item) => item.confirmed),
    },
    {
      label: 'Qoidalar',
      value: String(rules.length),
      helper: 'avtopilot chegaralari',
      ready: rules.length > 0,
    },
    {
      label: 'Ovoz',
      value: String(voice.length),
      helper: 'sotuvchi uslubi',
      ready: voice.length > 0,
    },
    {
      label: 'Juftliklar',
      value: String(pairs.length),
      helper: 'mijoz -> javob namunalari',
      ready: pairs.length > 0,
    },
  ]
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function sourceSummaryRank(summary: BrainSourceSummary) {
  const statusRank: Record<string, number> = {
    learned: 70,
    active: 70,
    indexed: 70,
    needs_review: 65,
    proposed: 65,
    learning: 50,
    queued: 40,
    retrying: 35,
    missing: 30,
    failed: 25,
    degraded: 25,
  }
  return (statusRank[summary.status] ?? 0)
    + Math.min(summary.sourceUnits, 10)
    + Math.min(summary.media, 10)
    + Math.min(summary.outputs.reduce((total, item) => total + item.count, 0), 10)
    + (summary.preview ? 2 : 0)
}

function sourceOutputs(
  facts: BusinessBrainFactReadModel[],
  sourceRef: string,
  sourceFactId: string,
) {
  const counts = new Map<string, number>()
  for (const fact of facts) {
    if (fact.fact_id === sourceFactId || surfaceForFact(fact) === 'sources') continue
    if (!factRefsSource(fact, sourceRef)) continue
    const label = sourceOutputLabel(surfaceForFact(fact))
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count }))
    .sort((left, right) => sourceOutputRank(left.label) - sourceOutputRank(right.label))
}

function factRefsSource(fact: BusinessBrainFactReadModel, sourceRef: string) {
  const normalized = sourceRef.trim()
  if (!normalized) return false
  return fact.source_refs.some((ref) => ref === normalized || ref.startsWith(`${normalized}/`) || ref.startsWith(`${normalized}:`))
}

function sourceOutputLabel(surface: BrainSurface) {
  if (surface === 'catalog') return 'Katalog'
  if (surface === 'knowledge') return 'Bilim'
  if (surface === 'rules') return 'Qoida'
  if (surface === 'voice') return 'Ovoz'
  if (surface === 'pairs') return 'Suhbat'
  return 'Kompaniya'
}

function sourceOutputRank(label: string) {
  const ranks: Record<string, number> = {
    Katalog: 1,
    Bilim: 2,
    Qoida: 3,
    Ovoz: 4,
    Suhbat: 5,
    Kompaniya: 6,
  }
  return ranks[label] ?? 99
}

function mergeSourceOutputs(
  left: Array<{ label: string; count: number }>,
  right: Array<{ label: string; count: number }>,
) {
  const counts = new Map<string, number>()
  for (const item of [...left, ...right]) {
    counts.set(item.label, Math.max(counts.get(item.label) ?? 0, item.count))
  }
  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => sourceOutputRank(a.label) - sourceOutputRank(b.label))
}

function stateFactTitle(fact: BusinessBrainFactReadModel, state: Record<string, unknown>) {
  const stateType = stringValue(state.state_type)
  const requiresReview = state.requires_review === true
  if (fact.fact_type.includes('payment') || stateType === 'payment') {
    return requiresReview ? 'To‘lovni tasdiqlash kerak' : 'To‘lov holati'
  }
  if (fact.fact_type.includes('delivery') || stateType === 'delivery') {
    return requiresReview ? 'Yetkazishni tekshirish kerak' : 'Yetkazish holati'
  }
  if (fact.fact_type.includes('order') || stateType === 'order') {
    return requiresReview ? 'Buyurtmani tekshirish kerak' : 'Buyurtma holati'
  }
  if (fact.fact_type.includes('customer')) return 'Mijoz holati'
  if (fact.fact_type.includes('company')) return 'Kompaniya ma’lumoti'
  return ''
}

function pairTitle(fact: BusinessBrainFactReadModel) {
  const customer = conversationPairParts(fact).customer
  if (!customer || customer.length < 8) return 'Suhbatdan o‘rganilgan javob'
  return `Mijoz: ${truncateText(customer, 54)}`
}

function pairSummary(fact: BusinessBrainFactReadModel) {
  if (fact.fact_type === 'conversation_pair_fact') {
    const { customer, seller } = conversationPairParts(fact)
    const lines = []
    if (customer) lines.push(`Mijoz: ${customer}`)
    if (seller) lines.push(`Sotuvchi javobi: ${seller}`)
    return lines.join('\n')
  }
  if (fact.fact_type === 'correction_episode_fact') {
    const value = objectValue(fact.value)
    const finalOutput = stringValue(value.final_output)
    const feedback = stringValue(value.human_feedback)
    const candidate = stringValue(value.candidate_output)
    const lines = []
    if (candidate) lines.push(`AI yozgan: ${candidate}`)
    if (feedback) lines.push(`Tuzatish: ${feedback}`)
    if (finalOutput) lines.push(`To‘g‘ri javob: ${finalOutput}`)
    return lines.join('\n')
  }
  return ''
}

function stateFactSummary(fact: BusinessBrainFactReadModel, state: Record<string, unknown>) {
  const stateType = stringValue(state.state_type)
  const reason = stringValue(state.reason_code).replaceAll('_', ' ')
  const requiresReview = state.requires_review === true
  const evidenceCount = fact.source_refs.filter((ref) => !isInternalAggregateRef(ref)).length
  const evidenceText = evidenceCount > 0 ? ` ${evidenceCount} ta dalil bor.` : ''
  if (fact.fact_type.includes('payment') || stateType === 'payment') {
    return requiresReview
      ? `OQIM to‘lovga o‘xshash dalil topdi. Pul tushganini tasdiqlang yoki rad eting.${evidenceText}`
      : `To‘lov holati suhbatdan ajratildi.${evidenceText}`
  }
  if (fact.fact_type.includes('delivery') || stateType === 'delivery') {
    return requiresReview
      ? `Mijoz yetkazish bo‘yicha javob kutyapti. Holatni tasdiqlang yoki rad eting.${evidenceText}`
      : `Yetkazish holati suhbatdan ajratildi.${evidenceText}`
  }
  if (fact.fact_type.includes('order') || stateType === 'order') {
    return requiresReview
      ? `Buyurtma holati aniq emas. Tasdiqlasangiz agent shu holatga tayanadi.${evidenceText}`
      : `Buyurtma holati suhbatdan ajratildi.${evidenceText}`
  }
  if (reason) return `OQIM suhbatdan shu holatni ajratdi: ${reason}.${evidenceText}`
  return evidenceText.trim()
}

function truncateText(value: string, maxLength: number) {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength - 1).trim()}…`
}

function isInternalAggregateRef(value: string) {
  return /:(sources|messages)$/i.test(value.trim())
}

function cleanPreview(value: string) {
  return value
    .replace(/\b(?:onboarding:source:\d+\/\d+|source_unit:[^:\s]+|message:\d+)\s*:\s*/gi, '')
    .replace(/\b(?:conversation|onboarding|brain|workspace):[^\s,]+/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function firstNonEmpty(...values: string[]) {
  return values.find((value) => value.trim().length > 0) ?? ''
}

function extractLabeledLine(value: string, labels: string[]) {
  if (!value) return ''
  const escaped = labels.map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  const pattern = new RegExp(`(?:^|\\n)\\s*(?:${escaped})\\s*:\\s*([\\s\\S]*?)(?=\\n\\s*(?:Oldingi kontekst|Previous context|Mijoz|Customer|Sotuvchi|Seller|Sotuvchi javobi|AI yozgan|Tuzatish|To.?g.?ri javob)\\s*:|$)`, 'i')
  return stringValue(value.match(pattern)?.[1])
}

function extractLabeledList(value: string, labels: string[]) {
  const section = extractLabeledLine(value, labels)
  if (!section) return []
  return section
    .split(/\n+/)
    .map((line) => line.replace(/^[-*]\s*/, '').trim())
    .filter(Boolean)
}

function cleanLabeledTranscript(value: string, mode: 'seller' | 'customer') {
  const labeled = mode === 'seller'
    ? extractLabeledLine(value, ['Sotuvchi', 'Seller', 'Sotuvchi javobi'])
    : extractLabeledLine(value, ['Mijoz', 'Customer'])
  return labeled || value
}

function degradedReasonsForFact(
  fact: BusinessBrainFactReadModel,
  processing: Record<string, unknown>,
) {
  const reasons = Array.isArray(processing.degraded_reasons)
    ? processing.degraded_reasons.filter((item): item is string => typeof item === 'string')
    : []
  return fact.status === 'degraded' && reasons.length === 0 ? ['degraded'] : reasons
}

function learningSourceFact(source: OnboardingSourceLearningSource): BusinessBrainFactReadModel {
  return {
    schema_version: 'business_brain_fact_read_model.v1',
    fact_id: source.fact_id || source.source_ref,
    workspace_id: 1,
    fact_type: 'business_source_fact',
    entity_ref: source.entity_ref || `workspace:source:${source.source_ref}`,
    value: {
      kind: source.kind,
      label: source.label,
      processing: {
        state: source.status,
        source_unit_count: source.source_unit_count,
        source_media_count: source.source_media_count,
        degraded_reasons: source.degraded_reasons,
      },
    },
    confidence: 1,
    status: source.status,
    risk_tier: 'low',
    source_refs: source.source_refs.length ? source.source_refs : [source.source_ref],
    freshness: { state: 'fresh' },
    valid_from: new Date(0).toISOString(),
  }
}
