import { useState, type ChangeEvent } from 'react'
import { Cube, PencilSimpleLine, ShieldCheck } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { OnboardingLearnedReviewProduct } from '@/lib/types'
import { reviewEvidenceCopy, reviewEvidenceLabel, reviewEvidenceMeta, reviewProductTitle } from './copy'
import type { LearnedReviewActionInput } from './types'

export interface ProductPendingSourceSignal {
  id: string
  title: string
  detail: string
  statusLabel: string
}

export function ProductPreviewTable({
  products,
  pendingSources = [],
  isRunning,
  isComplete,
  disabled,
  onReviewAction,
}: {
  products: OnboardingLearnedReviewProduct[]
  pendingSources?: ProductPendingSourceSignal[]
  isRunning: boolean
  isComplete: boolean
  disabled: boolean
  onReviewAction: (input: LearnedReviewActionInput) => void
}) {
  if (products.length === 0) {
    if (pendingSources.length > 0) {
      return (
        <div className="grid gap-3 rounded-lg border border-dashed border-border px-4 py-4 text-sm">
          <div>
            <p className="font-medium">Katalog dalili topildi</p>
            <p className="mt-1 leading-6 text-muted-foreground">
              OQIM mahsulot kartasini hali yakuniy haqiqatga aylantirmadi. Qaysi manbadan nima topilganini shu yerda ko‘ring.
            </p>
          </div>
          <div className="grid gap-2">
            {pendingSources.slice(0, 4).map((source) => (
              <div key={source.id} className="grid gap-1 rounded-md border border-border/80 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                <div className="min-w-0">
                  <p className="truncate font-medium">{source.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-muted-foreground">{source.detail}</p>
                </div>
                <Badge variant="outline" className="w-fit">
                  {source.statusLabel}
                </Badge>
              </div>
            ))}
          </div>
        </div>
      )
    }
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
        {isRunning
          ? 'Katalog ma’lumotlari qidirilyapti...'
          : isComplete
            ? 'Katalog uchun manba topilmadi. Fayl, sayt yoki Telegram kanal qo‘shsangiz shu yerda chiqadi.'
            : 'Katalog uchun manba qo‘shing.'}
      </div>
    )
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-56">Mahsulot nomi</TableHead>
          <TableHead>Tavsif</TableHead>
          <TableHead className="w-36">Manba</TableHead>
          <TableHead className="w-20" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {products.slice(0, 4).map((product) => (
          <ProductPreviewRow
            key={product.fact_id}
            product={product}
            mergeIntoRef={products.find((candidate) => candidate.product_ref !== product.product_ref)?.product_ref}
            initiallyEditing={false}
            disabled={disabled}
            onReviewAction={onReviewAction}
          />
        ))}
      </TableBody>
    </Table>
  )
}

function ProductPreviewRow({
  product,
  mergeIntoRef,
  initiallyEditing,
  disabled,
  onReviewAction,
}: {
  product: OnboardingLearnedReviewProduct
  mergeIntoRef?: string
  initiallyEditing: boolean
  disabled: boolean
  onReviewAction: (input: LearnedReviewActionInput) => void
}) {
  const visibleTitle = reviewProductTitle(product)
  const [isEditing, setIsEditing] = useState(initiallyEditing)
  const [title, setTitle] = useState(visibleTitle)
  const mediaUrl = typeof product.media[0]?.url === 'string' ? product.media[0].url : null
  const canSaveEdit = title.trim().length >= 2 && title.trim() !== visibleTitle.trim()
  const firstEvidence = product.source_evidence?.[0]

  return (
    <>
      <TableRow>
        <TableCell className="w-56 whitespace-normal">
          <div className="flex items-center gap-3">
            <div className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-lg bg-muted">
              {mediaUrl ? (
                <img src={mediaUrl} alt="" className="size-full object-cover" />
              ) : (
                <Cube className="size-5 text-muted-foreground" />
              )}
            </div>
            <div className="min-w-0">
              <div className="font-medium leading-5">{visibleTitle}</div>
              {product.category ? <div className="mt-1 text-xs text-muted-foreground">{product.category}</div> : null}
            </div>
          </div>
        </TableCell>
        <TableCell className="whitespace-normal leading-6 text-muted-foreground">
          {product.description || 'Tavsif topilsa shu yerda chiqadi.'}
        </TableCell>
        <TableCell className="w-36 whitespace-normal">
          <div className="flex max-w-40 flex-col items-start gap-1">
            <Badge variant="outline" className="max-w-full truncate">
              {firstEvidence ? reviewEvidenceLabel(firstEvidence) : reviewEvidenceCopy(product.source_refs.length, product.confidence)}
            </Badge>
            {firstEvidence || (product.source_evidence?.length ?? 0) > 1 ? (
              <span className="line-clamp-2 text-xs text-muted-foreground">
                {firstEvidence ? reviewEvidenceMeta(firstEvidence) ?? `${product.source_evidence?.length ?? 1} dalil` : `${product.source_evidence?.length ?? 0} dalil`}
              </span>
            ) : null}
          </div>
        </TableCell>
        <TableCell className="w-20">
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              size="icon-xs"
              variant="outline"
              aria-label="Tahrirlash"
              disabled={disabled}
              onClick={() => setIsEditing((value) => !value)}
            >
              <PencilSimpleLine />
            </Button>
            <Button
              type="button"
              size="icon-xs"
              aria-label="Tasdiqlash"
              disabled={disabled}
              onClick={() => onReviewAction({
                action: 'approve',
                targetType: 'product',
                targetRef: product.product_ref,
              })}
            >
              <ShieldCheck />
            </Button>
          </div>
        </TableCell>
      </TableRow>
      {isEditing ? (
        <TableRow>
          <TableCell colSpan={4} className="whitespace-normal bg-muted/30">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-end">
              <div className="grid gap-2">
                <Label htmlFor={`review-title-${product.fact_id}`}>Mahsulot nomi</Label>
                <Input
                  id={`review-title-${product.fact_id}`}
                  value={title}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => setTitle(event.target.value)}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={disabled || !canSaveEdit}
                onClick={() => onReviewAction({
                  action: 'edit',
                  targetType: 'fact',
                  targetRef: product.fact_id,
                  valuePatch: { title: title.trim() },
                })}
              >
                Tuzatib tasdiqlash
              </Button>
              {mergeIntoRef ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => onReviewAction({
                    action: 'merge',
                    targetType: 'product',
                    targetRef: product.product_ref,
                    mergeIntoRef,
                  })}
                >
                  Birlashtirish
                </Button>
              ) : null}
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}
