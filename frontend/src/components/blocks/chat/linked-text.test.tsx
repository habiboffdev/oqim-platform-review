// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { LinkedText } from './linked-text'

describe('LinkedText', () => {
  it('renders Telegram custom emoji entities as inline images while preserving links', () => {
    render(
      <LinkedText
        text="Look 😘 https://oqim.ai"
        textEntities={[
          {
            type: 'custom_emoji',
            offset: 5,
            length: 2,
            document_id: '123456789',
          },
        ]}
      />,
    )

    expect(screen.getByRole('img', { name: '😘' }).getAttribute('src')).toBe('/api/media/custom-emoji/123456789')
    expect(screen.getByRole('link', { name: 'https://oqim.ai' }).getAttribute('href')).toBe('https://oqim.ai')
  })
})
