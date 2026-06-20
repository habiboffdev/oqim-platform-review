import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import * as Icons from './nav-icons'

const ICON_NAMES = [
  'ChatIcon', 'BrainIcon', 'DatabaseIcon', 'RobotIcon', 'SparkIcon', 'SendIcon',
  'ChecklistIcon', 'PlugIcon', 'GearIcon', 'CaretDownIcon', 'SearchIcon',
] as const

describe('nav-icons', () => {
  it.each(ICON_NAMES)('%s renders a thin-line svg using currentColor', (name) => {
    const Icon = (Icons as Record<string, Icons.IconComponent>)[name]
    const { container } = render(<Icon className="size-4 shrink-0" />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg?.getAttribute('stroke')).toBe('currentColor')
    expect(svg?.getAttribute('stroke-width')).toBe('1.5')
    expect(svg?.getAttribute('fill')).toBe('none')
    expect(svg?.getAttribute('class')).toContain('size-4')
    expect(svg?.querySelector('path, circle, ellipse, rect')).not.toBeNull()
  })
})
