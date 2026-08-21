/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        theme.test.ts
 * Purpose:          Keeps the design system honest: the Tailwind theme must agree with the token
 *                   table, and no feature component may name a raw design value.
 * Interacts With:   src/theme/tokens.ts, tailwind.config.js, every component
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Design Systems, Drift Prevention
 * Tools:            Vitest, Node fs
 *
 * `tailwind.config.js` duplicates the values in `tokens.ts` because PostCSS loads it in a plain
 * Node context that cannot resolve a `.ts` module. These tests are what make that duplication safe:
 * a hex changed in one file and not the other fails here rather than shipping two palettes.
 *
 * The second half is the rule the whole design system rests on — feature components compose
 * primitives and never hardcode a hex, radius, duration, or font size. A comment saying so is a
 * suggestion; a failing test is a rule.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import tailwindConfig from '../../tailwind.config.js'
import {
  BORDERS,
  BRAND_GRADIENT,
  CHART_DOMAINS,
  COMPONENT_HEIGHTS,
  CONSOLE_FONT_STACK,
  CONSOLE_HEIGHTS,
  DURATIONS,
  FONT_SIZE_NAMES,
  FOUNDATION_COLORS,
  GRADIENTS,
  RADII,
  SHADOWS,
  SPARKLINE_WINDOW,
  STATE_LABEL,
  STATE_TONE,
  STATUS_COLORS,
  SURFACES,
  TEXT,
  WIDTHS,
} from '../../src/theme/tokens'
import { IncidentState } from '../../src/types/contracts.gen'

const theme = (tailwindConfig as { theme: { extend: Record<string, Record<string, unknown>> } }).theme.extend
const colors = theme.colors as Record<string, string>

const COMPONENTS_DIR = join(__dirname, '..', '..', 'src', 'components')

/** Every feature component — the files directly in `components/`, excluding the `ui/` primitives. */
function featureComponents(): Array<{ name: string; source: string }> {
  const components = readdirSync(COMPONENTS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.tsx'))
    .map((entry) => ({
      name: entry.name,
      source: readFileSync(join(COMPONENTS_DIR, entry.name), 'utf8'),
    }))
  return [
    ...components,
    { name: 'App.tsx', source: readFileSync(join(__dirname, '..', '..', 'src', 'App.tsx'), 'utf8') },
  ]
}

/** Strips comments, so prose quoting a hex or a duration is not mistaken for a hardcoded value. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

describe('token table and Tailwind theme agree', () => {
  it('uses the approved TripleTen light-shell and console palette', () => {
    expect(FOUNDATION_COLORS).toEqual({
      page: '#FFFFFF',
      secondary: '#F2F1EE',
      ink: '#1A1A1A',
      raised: '#2A2A2A',
      accent: '#FF976B',
      console: '#F2F1EE',
      'console-output': '#FFFFFF',
    })
    expect(SURFACES).toEqual({
      'surface-0': '#FFFFFF',
      'surface-1': '#F2F1EE',
      'surface-2': '#2A2A2A',
    })
    expect(TEXT.primary).toBe('#1A1A1A')
    expect(TEXT.console).toBe('#F2F1EE')
    expect(STATUS_COLORS).toEqual({
      healthy: '#3AA65E',
      alarm: '#ED6F68',
      pending: '#FFA800',
      active: '#3F96F3',
      guard: '#8754FD',
    })
  })

  it('exports every status colour', () => {
    for (const [name, hex] of Object.entries(STATUS_COLORS)) {
      expect(colors[name], `status colour ${name}`).toBe(hex)
    }
  })

  it('exports every surface', () => {
    for (const [name, hex] of Object.entries(SURFACES)) {
      expect(colors[name], `surface ${name}`).toBe(hex)
    }
  })

  it('exports every text weight', () => {
    for (const [name, hex] of Object.entries(TEXT)) {
      expect(colors[`text-${name}`], `text-${name}`).toBe(hex)
    }
  })

  it('exports both border weights', () => {
    expect(theme.borderColor).toEqual(BORDERS)
  })

  it('exports the radius scale', () => {
    expect(theme.borderRadius).toEqual(RADII)
  })

  it('exports the fixed console heights', () => {
    expect(theme.height).toEqual({ ...CONSOLE_HEIGHTS, ...COMPONENT_HEIGHTS })
  })

  it('exports every console height as a minimum as well', () => {
    // An expanded console swaps its fixed height for the same value as a floor, and `min-h-console-*`
    // has to be a real utility for that swap to do anything: it was not once, and an opened
    // disclosure made the frame collapse to its content instead of growing.
    expect(theme.minHeight).toEqual({
      ...CONSOLE_HEIGHTS,
      'console-footer': COMPONENT_HEIGHTS['console-footer'],
    })
  })

  it('gives all four consoles one height, keeping 480px at desktop', () => {
    // Uniformity was reached by raising the other three rather than by shrinking the worker, whose
    // log area had been deliberately doubled.
    expect(CONSOLE_HEIGHTS).toEqual({
      'console-mobile': '360px',
      'console-tablet': '420px',
      'console-desktop': '480px',
    })
  })

  it('exports the drawer width', () => {
    expect(theme.width).toEqual(WIDTHS)
  })

  it('exports the offset shadow scale', () => {
    expect(theme.boxShadow).toEqual(SHADOWS)
  })

  it('exports the console output face', () => {
    const families = theme.fontFamily as Record<string, string[]>
    expect(families.console).toEqual(CONSOLE_FONT_STACK)
    expect(families.console[0]).toBe('"Courier New"')
  })

  it('exports the brand gradient', () => {
    expect(theme.backgroundImage).toEqual(GRADIENTS)
    // Sampled from tripleten.com rather than invented, and the blue end is the readable one.
    expect(GRADIENTS['scenario-trigger']).toContain(BRAND_GRADIENT.from)
    expect(GRADIENTS['scenario-trigger']).toContain(BRAND_GRADIENT.to)
  })

  it('names every font size, so cn can tell a size from a colour', () => {
    // The list in `tokens.ts` is what `lib/cn.ts` hands to tailwind-merge. A size added to the
    // config and not to that list becomes unmergeable again — which is how a `text-` size got
    // silently dropped and a console rendered at the wrong size.
    expect([...FONT_SIZE_NAMES].sort()).toEqual(Object.keys(theme.fontSize as object).sort())
  })

  it('gives streamed console output a tighter line height than shell log text', () => {
    const sizes = theme.fontSize as Record<string, [string, { lineHeight: string }]>
    expect(sizes['console-line'][0]).toBe(sizes.log[0])
    expect(Number.parseFloat(sizes['console-line'][1].lineHeight)).toBeLessThan(
      Number.parseFloat(sizes.log[1].lineHeight),
    )
  })

  it('exports the durations the components actually use', () => {
    const durations = theme.transitionDuration as Record<string, string>
    expect(durations.metric).toBe(DURATIONS.metric)
    expect(durations.status).toBe(DURATIONS.status)
    expect(durations['panel-enter']).toBe(DURATIONS.panelEnter)
    expect(durations['log-append']).toBe(DURATIONS.logAppend)
    expect(durations['redaction-fade']).toBe(DURATIONS.redactionFade)
    expect(durations['banner-enter']).toBe(DURATIONS.bannerEnter)
  })

  it('pulses the HITL glow at the documented interval', () => {
    // Glow, never scale — a resizing button is a moving target.
    const animations = theme.animation as Record<string, string>
    expect(animations['hitl-pulse']).toContain(DURATIONS.hitlPulse)
    expect(animations['hitl-pulse']).toContain('infinite')
  })

  it('declares no colour the token table does not name', () => {
    // The other direction of the same check. A colour reachable through a class but absent from
    // `tokens.ts` is a palette entry no one reviewed.
    const known = new Set([
      ...Object.keys(STATUS_COLORS),
      ...Object.keys(FOUNDATION_COLORS),
      ...Object.keys(SURFACES),
      ...Object.keys(TEXT).map((name) => `text-${name}`),
    ])
    expect(Object.keys(colors).filter((name) => !known.has(name))).toEqual([])
  })
})

describe('state mappings are exhaustive', () => {
  it('assigns a tone to every incident state', () => {
    for (const state of Object.values(IncidentState)) {
      expect(STATE_TONE[state], state).toBeDefined()
      expect(STATUS_COLORS[STATE_TONE[state]], state).toBeDefined()
    }
  })

  it('assigns a label to every incident state', () => {
    for (const state of Object.values(IncidentState)) {
      expect(STATE_LABEL[state], state).toBeTruthy()
    }
  })

  it('paints the guardrail states cyan, not red', () => {
    // A guardrail that held is a success. Painting these red would tell the exact opposite of the
    // story Scenario 4 exists to tell.
    expect(STATE_TONE.EXPLOIT_INTERCEPTED).toBe('guard')
    expect(STATE_TONE.SECURITY_CONTAINED).toBe('guard')
  })

  it('distinguishes the security badge from an outage badge', () => {
    expect(STATE_LABEL.EXPLOIT_INTERCEPTED).toBe('Unsafe request blocked')
    expect(STATE_LABEL.EXPLOIT_INTERCEPTED).not.toContain('outage')
  })
})

describe('chart conventions', () => {
  it('fixes a y-domain for every charted metric', () => {
    // An auto-scaling axis is the single most common way a live dashboard lies: it makes a 4,820ms
    // spike look identical to a 48ms baseline.
    for (const [metric, domain] of Object.entries(CHART_DOMAINS)) {
      expect(domain, metric).toHaveLength(2)
      expect(domain[1], metric).toBeGreaterThan(domain[0])
    }
  })

  it('gives p99 latency headroom above its documented peak', () => {
    expect(CHART_DOMAINS.latency_p99_ms[1]).toBeGreaterThan(4820)
  })

  it('windows the sparklines to 60 samples', () => {
    expect(SPARKLINE_WINDOW).toBe(60)
  })
})

describe('feature components own no design values', () => {
  const components = featureComponents()

  it('finds the feature components to check', () => {
    // Guards the gate: a glob that silently matched nothing would make every assertion below pass
    // vacuously, which is worse than having no test.
    expect(components.length).toBeGreaterThanOrEqual(9)
  })

  it.each(components)('$name contains no hex colour literal', ({ source }) => {
    expect(stripComments(source).match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull()
  })

  it.each(components)('$name contains no raw millisecond duration', ({ source }) => {
    // `style={{ animationDelay }}` in AgentReasoningView is the one computed exception, and it
    // reads its unit from the token table rather than naming a number.
    expect(stripComments(source).match(/\b\d+ms\b/g)).toBeNull()
  })

  it.each(components)('$name reaches colour only through a Tailwind class', ({ source }) => {
    expect(stripComments(source)).not.toMatch(/\b(rgba?|hsla?)\(/)
  })

  it.each(components)('$name contains no arbitrary layout, radius, tracking, or font-size class', ({ source }) => {
    expect(stripComments(source)).not.toMatch(/(?:max-w|tracking|rounded|text)-\[[^\]]+\]/)
    expect(stripComments(source)).not.toMatch(/\b(?:sm:|md:|lg:|xl:)?text-(?:xs|sm|base|lg|xl|[2-9]xl)\b/)
  })

  it.each(components)('$name contains no arbitrary height except the 44px target minimum', ({ source }) => {
    const arbitraryHeights = stripComments(source).match(
      /(?:^|[\s"'`])(?:sm:|md:|lg:|xl:)?(?:h|min-h|max-h)-\[[^\]]+\]/gm,
    ) ?? []
    expect(
      arbitraryHeights
        .map((value) => value.replace(/^[\s"'`]+/, ''))
        .filter((value) => value !== 'min-h-[44px]'),
    ).toEqual([])
  })
})
