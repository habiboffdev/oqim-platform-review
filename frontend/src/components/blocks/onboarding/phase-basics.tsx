import { type ChangeEvent, type FormEvent } from 'react'
import { ArrowRight } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { uz } from '@/lib/uz'
import { CATEGORY_OPTIONS, REVENUE_OPTIONS } from './constants'
import { OptionGroup } from './option-group'
import type { RevenueBandKey } from './types'

export function BusinessBasicsStep({
  businessName,
  category,
  revenueBand,
  offerSummary,
  region,
  onBusinessNameChange,
  onCategoryChange,
  onRevenueBandChange,
  onOfferSummaryChange,
  onRegionChange,
  onNext,
}: {
  businessName: string
  category: string
  revenueBand: RevenueBandKey
  offerSummary: string
  region: string
  onBusinessNameChange: (value: string) => void
  onCategoryChange: (value: string) => void
  onRevenueBandChange: (value: RevenueBandKey) => void
  onOfferSummaryChange: (value: string) => void
  onRegionChange: (value: string) => void
  onNext: () => void
}) {
  const canContinue = businessName.trim().length >= 2 && offerSummary.trim().length >= 3

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (canContinue) onNext()
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="business-name">{uz.onboarding.businessName}</Label>
        <Input
          id="business-name"
          value={businessName}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onBusinessNameChange(e.target.value)}
          placeholder={uz.onboarding.businessNamePlaceholder}
          autoComplete="organization"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="business-category">{uz.onboarding.businessCategory}</Label>
        <Select value={category} onValueChange={(value) => value && onCategoryChange(value)}>
          <SelectTrigger id="business-category" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {CATEGORY_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="offer-summary">{uz.onboarding.offerSummary}</Label>
        <Textarea
          id="offer-summary"
          value={offerSummary}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onOfferSummaryChange(e.target.value)}
          placeholder={uz.onboarding.offerSummaryPlaceholder}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="business-region">{uz.onboarding.region}</Label>
        <Input
          id="business-region"
          value={region}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onRegionChange(e.target.value)}
          placeholder={uz.onboarding.regionPlaceholder}
          autoComplete="address-level1"
        />
      </div>

      <div className="flex flex-col gap-2">
        <OptionGroup
          label={uz.onboarding.businessRevenue}
          options={REVENUE_OPTIONS}
          value={revenueBand}
          onChange={onRevenueBandChange}
        />
      </div>

      <Button type="submit" size="lg" disabled={!canContinue} className="mt-2">
        {uz.onboarding.businessContinue}
        <ArrowRight size={16} weight="thin" />
      </Button>
    </form>
  )
}
