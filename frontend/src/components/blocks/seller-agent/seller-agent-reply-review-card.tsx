import { useEffect, useState, type ChangeEvent } from 'react'
import {
  ArrowClockwise,
  CheckCircle,
  Circle,
  PaperPlaneTilt,
  PencilSimple,
  Sparkle,
  Spinner as SpinnerIcon,
  WarningCircle,
  X,
} from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import {
  useApproveSellerAgentReply,
  useDismissSellerAgentReply,
  useEditSellerAgentReply,
  useRegenerateSellerAgentReply,
} from '@/hooks/use-seller-agent-replies'
import { cn, formatRelativeTime } from '@/lib/utils'
import { GREEN_CONFIDENCE_THRESHOLD, YELLOW_CONFIDENCE_THRESHOLD } from '@/lib/constants'
import type { SellerAgentReply, SellerAgentLearningRuntimeProjection } from '@/lib/types'

type SellerAgentReplyReviewCardMode = 'list' | 'panel'

interface SellerAgentReplyReviewCardProps {
  reply: SellerAgentReply
  mode?: SellerAgentReplyReviewCardMode
  selected?: boolean
  onSelect?: () => void
}

const DISMISS_REASONS = ['bad_tone', 'incorrect_fact', 'other'] as const

function replyText(reply: SellerAgentReply) {
  return reply.split_messages?.length
    ? reply.split_messages.join('\n')
    : reply.final_content || reply.draft_content
}

function confidenceTone(score: number) {
  if (score >= GREEN_CONFIDENCE_THRESHOLD) return 'bg-emerald-500/15 text-emerald-700'
  if (score >= YELLOW_CONFIDENCE_THRESHOLD) return 'bg-amber-500/15 text-amber-700'
  return 'bg-rose-500/15 text-rose-700'
}

function confidenceLabel(score: number) {
  return `${Math.round(score * 100)}% ishonch`
}

function learningLabel(runtime: SellerAgentLearningRuntimeProjection | null | undefined) {
  switch (runtime?.state) {
    case 'learned':
      return "O'rganildi"
    case 'queued':
      return "O'rganish navbatda"
    case 'skipped':
      return "O'rganish o'tkazildi"
    case 'failed':
      return "O'rganish xatosi"
    case 'not_applicable':
    default:
      return "O'rganish boshlanmagan"
  }
}

function learningTone(runtime: SellerAgentLearningRuntimeProjection | null | undefined) {
  switch (runtime?.state) {
    case 'learned':
      return 'bg-emerald-500/15 text-emerald-700'
    case 'queued':
      return 'bg-amber-500/15 text-amber-700'
    case 'failed':
      return 'bg-rose-500/15 text-rose-700'
    default:
      return 'bg-muted text-muted-foreground'
  }
}

function nextActionLabel(runtime: SellerAgentLearningRuntimeProjection | null | undefined) {
  switch (runtime?.next_action) {
    case 'retry':
      return 'Keyingi amal: qayta urinish'
    case 'wait':
      return 'Keyingi amal: kutish'
    case 'none':
    default:
      return "Keyingi amal yo'q"
  }
}

function shortNextAction(action: string | undefined) {
  if (action === 'retry') return 'qayta urinish'
  if (action === 'wait') return 'kutish'
  return 'tayyor'
}

function triggerLabel(reply: SellerAgentReply) {
  if (reply.trigger_message_text) return reply.trigger_message_text
  if (reply.trigger_type === 'follow_up') return 'Qayta aloqa sababi'
  if (reply.trigger_id) return `Sabab #${reply.trigger_id}`
  if (reply.trigger_message_id) return `Sabab #${reply.trigger_message_id}`
  return 'Javob nima uchun chiqqani hali ko‘rsatilmagan'
}

function deliveryRuntimeLabel(status: string) {
  if (status === 'sent') return 'yuborildi'
  if (status === 'failed') return 'xato'
  if (status === 'sending') return 'yuborilmoqda'
  if (status === 'uncertain') return 'aniqlanmoqda'
  return status
}

function deliveryLabel(reply: SellerAgentReply) {
  const runtime = reply.delivery_runtime
  if (runtime) {
    return `${deliveryRuntimeLabel(runtime.customer_status)} · ${shortNextAction(runtime.next_action)}`
  }
  if (reply.status === 'sending' || reply.status === 'approved') return 'Yuborilmoqda'
  if (reply.status === 'sent') return 'Yuborildi'
  if (reply.status === 'delivery_failed') return 'Yuborish xatosi'
  if (reply.status === 'delivery_unknown') return 'Telegram tasdig‘i kutilmoqda'
  return "Yuborish boshlanmagan"
}

function deliveryTone(reply: SellerAgentReply) {
  const runtime = reply.delivery_runtime
  if (runtime?.customer_status === 'sent') return 'bg-emerald-500/15 text-emerald-700'
  if (runtime?.customer_status === 'failed' || reply.status === 'delivery_failed') return 'bg-rose-500/15 text-rose-700'
  if (
    runtime?.customer_status === 'uncertain' ||
    reply.status === 'delivery_unknown' ||
    reply.status === 'sending' ||
    reply.status === 'approved'
  ) {
    return 'bg-amber-500/15 text-amber-700'
  }
  return 'bg-muted text-muted-foreground'
}

export function SellerAgentReplyReviewCard({
  reply,
  mode = 'list',
  selected = false,
  onSelect,
}: SellerAgentReplyReviewCardProps) {
  const approve = useApproveSellerAgentReply()
  const edit = useEditSellerAgentReply()
  const dismiss = useDismissSellerAgentReply()
  const regenerate = useRegenerateSellerAgentReply()

  const currentReplyText = replyText(reply)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(currentReplyText)
  const [rejecting, setRejecting] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [instruction, setInstruction] = useState('')

  useEffect(() => {
    setEditing(false)
    setEditText(currentReplyText)
    setRejecting(false)
    setRegenerating(false)
    setInstruction('')
  }, [reply.id, currentReplyText])

  const isActing = approve.isPending || edit.isPending || dismiss.isPending || regenerate.isPending
  const compact = mode === 'panel'
  const learningRuntime = reply.learning_runtime
  const blocked = learningRuntime?.state === 'failed'

  function approveOrEdit() {
    const trimmed = editText.trim()
    if (editing && trimmed && trimmed !== currentReplyText) {
      edit.mutate({ replyId: reply.id, content: trimmed }, {
        onSuccess: () => setEditing(false),
      })
      return
    }
    approve.mutate(reply.id)
  }

  function submitRegenerate() {
    regenerate.mutate({
      replyId: reply.id,
      instruction: instruction.trim() || undefined,
    }, {
      onSuccess: () => {
        setRegenerating(false)
        setInstruction('')
      },
    })
  }

  return (
    <Card
      size="sm"
      onClick={onSelect}
      className={cn(
        'gap-0 rounded-lg border border-border/60 bg-background py-0 transition-shadow',
        onSelect && 'cursor-pointer hover:shadow-sm',
        selected && 'ring-2 ring-foreground/20',
      )}
    >
      <CardContent className={cn('px-4 py-4', compact && 'px-3 py-3')}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-muted-foreground">
                JAVOB-{reply.id}
              </span>
              <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium uppercase', confidenceTone(reply.confidence_score))}>
                {confidenceLabel(reply.confidence_score)}
              </span>
              {blocked && (
                <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase text-amber-700">
                  <WarningCircle className="size-3" weight="thin" />
                  Diqqat
                </span>
              )}
            </div>
            <h3 className="mt-2 text-sm font-semibold leading-snug">
              {reply.customer_name || `Suhbat #${reply.conversation_id}`}
            </h3>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatRelativeTime(reply.created_at)}
          </span>
        </div>

        <div className="mt-3 rounded-md border border-border/60 bg-foreground/[0.02] px-3 py-2">
          <div className="text-[11px] font-medium text-muted-foreground">Sabab</div>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {triggerLabel(reply)}
          </p>
        </div>

        {editing ? (
          <div className="mt-3">
            <Textarea
              value={editText}
              onChange={(event) => setEditText(event.target.value)}
              rows={compact ? 4 : 5}
              className="text-sm leading-relaxed"
            />
          </div>
        ) : (
          <p className="mt-3 whitespace-pre-wrap rounded-md bg-muted/40 px-3 py-2.5 text-sm leading-relaxed text-foreground">
            {currentReplyText}
          </p>
        )}

        <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
          <div className="rounded-md border border-border/60 px-3 py-2">
            <div className="flex items-center gap-1.5">
              <Sparkle className="size-3.5" weight="thin" />
              <span className="font-medium">O'rganish</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium', learningTone(learningRuntime))}>
                {learningLabel(learningRuntime)}
              </span>
              {learningRuntime?.signal_id ? (
                <span className="text-[10px] text-muted-foreground">
                  dalil saqlandi
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {nextActionLabel(learningRuntime)}
            </p>
            {learningRuntime?.last_error ? (
              <p className="mt-1 line-clamp-2 text-[11px] text-rose-700">
                O‘rganish to‘xtadi. Tahrirni yana yuborsangiz, AI qayta urinadi.
              </p>
            ) : null}
          </div>

          <div className="rounded-md border border-border/60 px-3 py-2">
            <div className="flex items-center gap-1.5">
              <PaperPlaneTilt className="size-3.5" weight="thin" />
              <span className="font-medium">Yuborish</span>
            </div>
            <div className="mt-2">
              <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium uppercase', deliveryTone(reply))}>
                {deliveryLabel(reply)}
              </span>
            </div>
            {reply.delivery_runtime?.last_error ? (
              <p className="mt-1 line-clamp-2 text-[11px] text-rose-700">
                Telegram tasdig‘i kelmadi. Yuborilgan-yuborilmaganini tekshiring.
              </p>
            ) : null}
          </div>
        </div>

        {regenerating && (
          <div className="mt-3 flex gap-2">
            <Input
              value={instruction}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setInstruction(event.target.value)}
              placeholder="Qanday qayta yozilsin?"
              className="h-8 text-xs"
            />
            <Button size="sm" onClick={submitRegenerate} disabled={isActing} className="h-8 shrink-0">
              {regenerate.isPending ? (
                <SpinnerIcon className="size-3.5 animate-spin" weight="thin" />
              ) : (
                <ArrowClockwise className="size-3.5" weight="thin" />
              )}
              Qayta
            </Button>
          </div>
        )}

        {rejecting && (
          <div className="mt-3 flex flex-wrap gap-1.5 rounded-md bg-muted/40 p-2">
            {DISMISS_REASONS.map((reason) => (
              <button
                key={reason}
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  dismiss.mutate({ replyId: reply.id, reason })
                }}
                className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] font-medium hover:border-foreground/20"
              >
                {reason === 'bad_tone' ? 'Ohang noto\'g\'ri' : reason === 'incorrect_fact' ? 'Fakt xato' : 'Boshqa'}
              </button>
            ))}
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={(event) => {
              event.stopPropagation()
              approveOrEdit()
            }}
            disabled={isActing}
            className="h-8 gap-1.5"
          >
            {approve.isPending || edit.isPending ? (
              <SpinnerIcon className="size-3.5 animate-spin" weight="thin" />
            ) : (
              <PaperPlaneTilt className="size-3.5" weight="thin" />
            )}
            {editing ? 'Saqlab yuborish' : 'Tasdiqlash'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={(event) => {
              event.stopPropagation()
              setEditing((value) => !value)
              setEditText(currentReplyText)
            }}
            disabled={isActing}
            className="h-8 gap-1.5"
          >
            <PencilSimple className="size-3.5" weight="thin" />
            {editing ? 'Bekor qilish' : 'Tahrirlash'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={(event) => {
              event.stopPropagation()
              setRegenerating((value) => !value)
            }}
            disabled={isActing}
            className="h-8 gap-1.5"
          >
            <ArrowClockwise className="size-3.5" weight="thin" />
            Qayta yozish
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={(event) => {
              event.stopPropagation()
              setRejecting((value) => !value)
            }}
            disabled={isActing}
            className="ml-auto h-8 gap-1.5 text-muted-foreground hover:text-destructive"
          >
            <X className="size-3.5" weight="thin" />
            Rad etish
          </Button>
        </div>

        <ol className="relative mt-4 border-t border-border/60 pt-3">
          <li className="grid grid-cols-[20px_1fr_auto] items-start gap-2 py-1.5">
            <Circle className="mt-1 size-3 text-muted-foreground" weight="fill" />
            <span className="text-xs text-muted-foreground">Mijoz xabari qabul qilindi</span>
            <span className="text-[10px] text-muted-foreground">{reply.channel || 'telegram_dm'}</span>
          </li>
          <li className="grid grid-cols-[20px_1fr_auto] items-start gap-2 py-1.5">
            <CheckCircle className="mt-0.5 size-4 text-emerald-600" weight="thin" />
            <span className="text-xs text-muted-foreground">AI javob taklif qildi</span>
            <span className="text-[10px] text-muted-foreground">AI</span>
          </li>
          <li className="grid grid-cols-[20px_1fr_auto] items-start gap-2 py-1.5">
            <Circle className="mt-1 size-3 text-muted-foreground" weight="fill" />
            <span className="text-xs text-muted-foreground">{learningLabel(learningRuntime)}</span>
            <span className="text-[10px] text-muted-foreground">{shortNextAction(learningRuntime?.next_action)}</span>
          </li>
        </ol>
      </CardContent>
    </Card>
  )
}
