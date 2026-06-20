import { useState, type ChangeEvent } from 'react'
import { CheckCircle, PaperPlaneTilt } from '@phosphor-icons/react'

import { uz } from '@/lib/uz'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  useOwnerBotBindLink,
  useOwnerBotProvision,
  useOwnerBotStatus,
  useOwnerBotUnbind,
} from '@/hooks/use-owner-bot'

export function OwnerBotCard() {
  const { data: status } = useOwnerBotStatus()
  const provision = useOwnerBotProvision()
  const bindLink = useOwnerBotBindLink()
  const unbind = useOwnerBotUnbind()
  const [name, setName] = useState('')
  const [username, setUsername] = useState('')
  const [link, setLink] = useState<string | null>(null)

  const bound = status?.owner_chat_bound ?? false
  const provisioned = status?.provisioned ?? false
  const trimmedName = name.trim()

  return (
    <Card className="rounded-lg" size="sm">
      <CardHeader className="border-b border-border/70">
        <CardTitle className="flex items-center gap-2">
          <PaperPlaneTilt weight="fill" className="size-4" />
          {uz.settings.ownerBotTitle}
          {bound ? (
            <Badge className="ml-auto gap-1">
              <CheckCircle weight="fill" className="size-3" />
              {uz.settings.ownerBotConnected}
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription>{uz.settings.ownerBotDescription}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 pt-4">
        {!provisioned ? (
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (!trimmedName) return
              provision.mutate({ name: trimmedName, username: username.trim() || undefined })
            }}
          >
            <p className="text-sm text-muted-foreground">
              {uz.settings.ownerBotNotProvisioned}
            </p>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="owner-bot-name">{uz.settings.ownerBotNameLabel}</Label>
              <Input
                id="owner-bot-name"
                value={name}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setName(event.target.value)}
                placeholder={uz.settings.ownerBotNamePlaceholder}
                disabled={provision.isPending}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="owner-bot-username">{uz.settings.ownerBotUsernameLabel}</Label>
              <Input
                id="owner-bot-username"
                value={username}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setUsername(event.target.value)}
                placeholder={uz.settings.ownerBotUsernamePlaceholder}
                disabled={provision.isPending}
              />
              <p className="text-xs text-muted-foreground">
                {uz.settings.ownerBotUsernameHint}
              </p>
            </div>
            <Button type="submit" disabled={provision.isPending || !trimmedName}>
              {provision.isPending ? uz.settings.ownerBotCreating : uz.settings.ownerBotCreate}
            </Button>
          </form>
        ) : bound ? (
          <Button
            variant="outline"
            onClick={() => unbind.mutate()}
            disabled={unbind.isPending}
          >
            {uz.settings.ownerBotUnbind}
          </Button>
        ) : (
          <>
            <Button
              onClick={() =>
                bindLink.mutate(undefined, {
                  onSuccess: (data) => setLink(data.deep_link),
                })
              }
              disabled={bindLink.isPending}
            >
              {uz.settings.ownerBotConnect}
            </Button>
            {link ? (
              <div className="flex flex-col gap-2">
                <Button
                  variant="outline"
                  onClick={() => window.open(link, '_blank', 'noreferrer')}
                >
                  {uz.settings.ownerBotOpenLink}
                </Button>
                <p className="text-xs text-muted-foreground">
                  {uz.settings.ownerBotDoNotForward}
                </p>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}
