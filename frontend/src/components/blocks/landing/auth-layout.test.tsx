// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AuthLayout } from './auth-layout'

describe('AuthLayout', () => {
  it('uses seller-facing onboarding copy without architecture jargon', () => {
    render(
      <AuthLayout variant="onboarding">
        <div>Onboarding form</div>
      </AuthLayout>,
    )

    expect(screen.getByText('Bilim yig‘iladi')).toBeDefined()
    expect(screen.queryByText('Brain to‘ldiriladi')).toBeNull()
    expect(screen.queryByText('Brain')).toBeNull()
  })
})
