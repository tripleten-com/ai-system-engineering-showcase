/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        cn.test.ts
 * Purpose:          Guards the class merger against the failure that silently dropped a font size.
 * Interacts With:   src/lib/cn.ts, src/theme/tokens.ts
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Design Systems, Drift Prevention
 * Tools:            Vitest
 *
 * `tailwind-merge` only knows the stock Tailwind scales. Our type scale is custom, so every
 * `text-<name>` size looked like a `text-<colour>` to it and the merge kept one of the two. Nothing
 * in the source looked wrong; the console just rendered at the wrong size. These tests are the
 * reason that cannot come back quietly.
 */

import { describe, expect, it } from 'vitest'

import { cn } from '../../src/lib/cn'
import { FONT_SIZE_NAMES } from '../../src/theme/tokens'

describe('cn', () => {
  it('still resolves a genuine conflict last-wins', () => {
    // The reason `twMerge` is here at all: `<Panel className="p-0">` must not render `p-6 p-0`.
    expect(cn('p-6', 'p-0')).toBe('p-0')
    expect(cn('text-ink', 'text-alarm')).toBe('text-alarm')
  })

  it.each([...FONT_SIZE_NAMES])('keeps text-%s alongside a text colour', (size) => {
    const merged = cn(`text-${size}`, 'text-console-output')

    expect(merged).toContain(`text-${size}`)
    expect(merged).toContain('text-console-output')
  })

  it('keeps a size and a colour together in either order', () => {
    expect(cn('font-console text-console-line', 'text-console-output')).toBe(
      'font-console text-console-line text-console-output',
    )
    expect(cn('text-console-output', 'text-console-line')).toBe('text-console-output text-console-line')
  })

  it('still resolves two competing sizes to the last one', () => {
    // The group has to be a real group, not just an exemption: two sizes on one element is the
    // conflict twMerge exists to settle.
    expect(cn('text-log', 'text-body')).toBe('text-body')
  })
})
