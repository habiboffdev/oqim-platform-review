// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import { uz } from '@/lib/uz'

let statusData: {
  provisioned: boolean
  bot_username: string | null
  deep_link: string | null
  owner_chat_bound: boolean
}
const provisionMutate = vi.fn()
let provisionPending = false

vi.mock('@/hooks/use-owner-bot', () => ({
  useOwnerBotStatus: () => ({ data: statusData }),
  useOwnerBotProvision: () => ({ mutate: provisionMutate, isPending: provisionPending }),
  useOwnerBotBindLink: () => ({ mutate: vi.fn(), isPending: false }),
  useOwnerBotUnbind: () => ({ mutate: vi.fn(), isPending: false }),
}))

import { OwnerBotCard } from './owner-bot-card'

describe('OwnerBotCard create-bot form', () => {
  beforeEach(() => {
    provisionMutate.mockClear()
    provisionPending = false
    statusData = {
      provisioned: false,
      bot_username: null,
      deep_link: null,
      owner_chat_bound: false,
    }
  })

  it('provisions the bot with the typed name + username', () => {
    render(<OwnerBotCard />)

    fireEvent.change(screen.getByLabelText(uz.settings.ownerBotNameLabel), {
      target: { value: 'Biznes boshqaruv' },
    })
    fireEvent.change(screen.getByLabelText(uz.settings.ownerBotUsernameLabel), {
      target: { value: 'biznes_boshqaruv' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.settings.ownerBotCreate }))

    expect(provisionMutate).toHaveBeenCalledWith({
      name: 'Biznes boshqaruv',
      username: 'biznes_boshqaruv',
    })
  })

  it('omits username when left blank (OQIM picks one)', () => {
    render(<OwnerBotCard />)

    fireEvent.change(screen.getByLabelText(uz.settings.ownerBotNameLabel), {
      target: { value: 'Biznes' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.settings.ownerBotCreate }))

    expect(provisionMutate).toHaveBeenCalledWith({ name: 'Biznes', username: undefined })
  })

  it('will not provision with an empty name', () => {
    render(<OwnerBotCard />)
    fireEvent.click(screen.getByRole('button', { name: uz.settings.ownerBotCreate }))
    expect(provisionMutate).not.toHaveBeenCalled()
  })

  it('shows the connect button (not the form) once provisioned', () => {
    statusData = {
      provisioned: true,
      bot_username: 'oqim_test_bot',
      deep_link: 'https://t.me/oqim_test_bot',
      owner_chat_bound: false,
    }
    render(<OwnerBotCard />)

    expect(screen.queryByLabelText(uz.settings.ownerBotNameLabel)).toBeNull()
    expect(screen.getByRole('button', { name: uz.settings.ownerBotConnect })).toBeTruthy()
  })
})
