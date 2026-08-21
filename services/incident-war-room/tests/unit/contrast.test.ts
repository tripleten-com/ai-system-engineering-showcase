/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        contrast.test.ts
 * Purpose:          Computes WCAG contrast ratios from the token table and holds the palette to
 *                   the thresholds spa-design-guidelines.md §11 sets.
 * Interacts With:   src/theme/tokens.ts, every component
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Accessibility, Design Systems, Drift Prevention
 * Tools:            Vitest
 *
 * The light shell and dark console have separate text roles. Status colours are reserved for marks,
 * fills, and borders in the raised console rather than being used as small text on the pale shell.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { BRAND_GRADIENT, FOUNDATION_COLORS, STATUS_COLORS, SURFACES, TEXT } from '../../src/theme/tokens'

/** WCAG 2.1 relative luminance. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255)
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG 2.1 contrast ratio, 1:1 to 21:1. */
export function contrastRatio(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

/** §11 thresholds. */
const BODY_TEXT_MIN = 4.5
const NON_TEXT_MIN = 3.0

/** Pale panel surface in the light shell. */
const PANEL = SURFACES['surface-1']

/** Raised dark surface for consoles and status instrumentation. */
const RAISED = SURFACES['surface-2']

const COMPONENTS_DIR = join(__dirname, '..', '..', 'src', 'components')

function componentSources(): Array<{ name: string; source: string }> {
  const files: Array<{ name: string; source: string }> = []
  const walk = (dir: string, prefix: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) walk(path, `${prefix}${entry.name}/`)
      else if (entry.name.endsWith('.tsx')) {
        files.push({ name: `${prefix}${entry.name}`, source: readFileSync(path, 'utf8') })
      }
    }
  }
  walk(COMPONENTS_DIR, '')
  return files
}

describe('contrastRatio', () => {
  it('agrees with the WCAG reference values at the extremes', () => {
    // Guards the gate: a broken formula would make every assertion below pass vacuously.
    expect(contrastRatio('#FFFFFF', '#000000')).toBeCloseTo(21, 1)
    expect(contrastRatio('#000000', '#000000')).toBeCloseTo(1, 5)
    expect(contrastRatio('#777777', '#FFFFFF')).toBeCloseTo(4.48, 1)
  })

  it('is symmetric', () => {
    expect(contrastRatio(TEXT.primary, PANEL)).toBeCloseTo(contrastRatio(PANEL, TEXT.primary), 10)
  })
})

describe('text on a panel clears the body-text floor', () => {
  it.each([
    ['primary', TEXT.primary],
    ['secondary', TEXT.secondary],
  ])('text-%s reads on surface-1', (_name, colour) => {
    expect(contrastRatio(colour, PANEL)).toBeGreaterThanOrEqual(BODY_TEXT_MIN)
  })

  it.each([
    ['console', TEXT.console],
  ])('text-%s reads on the raised console', (_name, colour) => {
    expect(contrastRatio(colour, RAISED)).toBeGreaterThanOrEqual(BODY_TEXT_MIN)
  })
})

describe('status colours are legible console indicators', () => {
  it.each(Object.entries(STATUS_COLORS))('%s clears the indicator floor on the raised console', (_name, colour) => {
    expect(contrastRatio(colour, RAISED)).toBeGreaterThanOrEqual(NON_TEXT_MIN)
  })
})

describe('scenario launcher buttons', () => {
  it('keeps their copy in ink instead of inheriting an inaccessible status colour', () => {
    const source = componentSources().find(({ name }) => name === 'ScenarioControls.tsx')?.source ?? ''

    expect(source).toContain('text-ink')
    expect(source).not.toMatch(/bg-(?:pending|guard)\/10 text-(?:pending|guard)/)
    expect(contrastRatio(TEXT.primary, PANEL)).toBeGreaterThanOrEqual(BODY_TEXT_MIN)
  })

  it('distinguishes the scenarios by glyph, and tints none of them with a status colour', () => {
    // Colour in this UI is a run-state system (§1). Tinting a trigger would claim a state before a
    // run exists, so the four are told apart by the glyph naming the failing subsystem — which also
    // survives greyscale and a colour-blind viewer. `guard` does clear the 3:1 non-text floor across
    // the gradient, so this is a semantics rule rather than a contrast one.
    const source = componentSources().find(({ name }) => name === 'ScenarioControls.tsx')?.source ?? ''

    for (const glyph of ['Database', 'Zap', 'Inbox', 'ShieldAlert']) {
      expect(source, glyph).toContain(glyph)
    }
    expect(source).not.toMatch(/bg-(?:healthy|alarm|pending|active|guard)/)
    expect(source).toContain('bg-scenario-trigger')
  })
})

describe('machine output on the console body', () => {
  it('clears the body-text floor on the raised surface', () => {
    // This is body text, not an indicator: every streamed log, reasoning step, retrieval excerpt and
    // worker line is set in it, so 4.5:1 is the bar rather than 3:1.
    expect(contrastRatio(FOUNDATION_COLORS['console-output'], RAISED)).toBeGreaterThanOrEqual(BODY_TEXT_MIN)
  })

  it('stays distinguishable from the level colours that override it', () => {
    // A WARN or ERROR line drops the neutral output colour for a status one. If those read alike on
    // the console surface, level colouring communicates nothing.
    const neutral = contrastRatio(FOUNDATION_COLORS['console-output'], RAISED)
    for (const tone of ['alarm', 'pending'] as const) {
      expect(Math.abs(neutral - contrastRatio(STATUS_COLORS[tone], RAISED)), tone).toBeGreaterThan(1)
    }
  })
})

describe('the brand gradient on the scenario launcher', () => {
  it.each([
    ['peach', BRAND_GRADIENT.from],
    ['blue', BRAND_GRADIENT.to],
  ])('carries ink label text at the %s end', (_name, stop) => {
    // Both ends have to clear the floor, not just the average: the label sits across the whole fill.
    // This is why the blue end is a lightened brand blue — full-strength #1863DC reaches 3.2:1.
    expect(contrastRatio(TEXT.primary, stop)).toBeGreaterThanOrEqual(BODY_TEXT_MIN)
  })
})

describe('the muted token', () => {
  it('is kept out of feature components', () => {
    const offenders = componentSources()
      .filter(({ source }) => source.includes('text-text-muted'))
      .map(({ name }) => name)

    expect(offenders).toEqual([])
  })
})
