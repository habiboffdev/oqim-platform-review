import { useState, type ChangeEvent } from 'react'
import { Gear, FloppyDisk } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api-client'
import { uz } from '@/lib/uz'
import { toast } from 'sonner'
import type { User } from '@/lib/types'

interface WorkspaceSectionProps {
  user: User | null
}

export function WorkspaceSection({ user }: WorkspaceSectionProps) {
  const [workspaceName, setWorkspaceName] = useState(user?.name || user?.full_name || '')
  const [isSaving, setIsSaving] = useState(false)

  const hasChanges = workspaceName !== (user?.name || user?.full_name || '')

  async function handleSave() {
    setIsSaving(true)
    try {
      await api.patch('/api/auth/workspace', { name: workspaceName })
      toast.success(uz.common.saved)
    } catch {
      toast.error(uz.common.error)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center gap-2.5">
        <Gear size={18} weight="thin" className="text-muted-foreground" />
        <h2 className="text-sm font-medium">{uz.settings.workspace}</h2>
      </div>
      <div className="space-y-3">
        <div>
          <label className="mb-1.5 block text-xs text-muted-foreground">
            {uz.settings.workspaceName}
          </label>
          <Input
            value={workspaceName}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setWorkspaceName(e.target.value)}
            placeholder={uz.settings.workspaceNamePlaceholder}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs text-muted-foreground">
            {uz.auth.phone}
          </label>
          <p className="text-sm">{user?.phone_number || '—'}</p>
        </div>
        {hasChanges && (
          <div className="flex justify-end">
            <Button size="sm" onClick={handleSave} disabled={isSaving}>
              <FloppyDisk size={14} weight="thin" />
              {isSaving ? uz.settings.saving : uz.common.save}
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}
