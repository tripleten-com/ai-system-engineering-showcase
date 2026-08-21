/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        components/ui/index.tsx
 * Purpose:          The seven primitives from spa-design-guidelines.md §10. They own every
 *                   design value; feature components compose them and hardcode nothing.
 * Interacts With:   Every feature component
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Design Systems, Component Layering, Accessibility
 * Tools:            React 18, Tailwind CSS, Recharts
 *
 * One file rather than seven, because these are small and always used together — a feature
 * component importing four primitives from four files gains nothing. The split that matters is
 * primitives vs features, and that boundary is the directory.
 *
 * **These are the only components allowed to name a colour, radius, duration, or font size.**
 * `tests/unit/theme.test.ts` asserts no feature component contains a hex literal, which is what
 * keeps that rule true rather than aspirational.
 */

import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { Area, AreaChart, YAxis } from 'recharts'

import { useElementWidth } from '../../hooks/useElementWidth'
import { cn } from '../../lib/cn'
import {
  SPARKLINE_HEIGHT,
  SPARKLINE_WINDOW,
  STATE_LABEL,
  STATE_TONE,
  STATUS_COLORS,
  type StatusTone,
} from '../../theme/tokens'
import type { IncidentState } from '../../types/contracts.gen'

// Status tone → the Tailwind classes that express it. A lookup rather than string interpolation
// so Tailwind's content scanner can see every class that will ever be emitted.
const TONE_BORDER: Record<StatusTone, string> = {
  healthy: 'border-healthy/40',
  alarm: 'border-alarm/40',
  pending: 'border-pending/40',
  active: 'border-active/40',
  guard: 'border-guard/40',
}

const TONE_BG: Record<StatusTone, string> = {
  healthy: 'bg-healthy/10',
  alarm: 'bg-alarm/10',
  pending: 'bg-pending/10',
  active: 'bg-active/10',
  guard: 'bg-guard/10',
}

const TONE_DOT: Record<StatusTone, string> = {
  healthy: 'bg-healthy',
  alarm: 'bg-alarm',
  pending: 'bg-pending',
  active: 'bg-active',
  guard: 'bg-guard',
}

// ---------------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------------

interface PanelProps {
  title?: string
  tone?: StatusTone
  className?: string
  children: ReactNode
  /** Rendered at the panel title's trailing edge — a count, a badge, a control. */
  aside?: ReactNode
  /**
   * Named rather than spread as `data-testid`. TypeScript exempts hyphenated JSX attributes from
   * excess-property checking, so a `data-testid` passed to a component that does not declare it
   * compiles cleanly and then silently never reaches the DOM. An explicit prop cannot do that.
   */
  testId?: string
  /** Pulses the panel's status glow. Only `AWAITING_APPROVAL` uses it. */
  pulse?: boolean
}

/**
 * A workflow column, metric card, or terminal shell.
 *
 * Emphasis is a glow in a status colour, never a drop shadow: a `box-shadow` on `#0B0F19` is
 * invisible, so shadow-based elevation advice from light-mode systems does not port.
 */
export function Panel({ title, tone, className, children, aside, testId, pulse }: PanelProps) {
  return (
    <section
      data-testid={testId}
      className={cn(
        'rounded-lg border border-subtle bg-surface-1 p-4 lg:p-6',
        'animate-panel-enter',
        tone && TONE_BORDER[tone],
        tone && 'text-ink',
        pulse && 'animate-hitl-pulse',
        className,
      )}
    >
      {title && (
        <header className="mb-4 flex items-center justify-between gap-3">
          <h2 className="font-sans text-panel-title uppercase text-text-secondary">{title}</h2>
          {aside}
        </header>
      )}
      <div className="text-text-primary">{children}</div>
    </section>
  )
}

// ---------------------------------------------------------------------------------
// StatusBadge
// ---------------------------------------------------------------------------------

/**
 * The single source of truth for state → colour in the rendered UI.
 *
 * Always renders the state's *name* alongside its colour. That is an accessibility requirement
 * and it is also why Scenario 4 reads correctly: green gauges beside a cyan
 * `SECURITY EVENT (DEGRADED)` badge is a specific, legible combination that colour alone could
 * not express.
 */
export function StatusBadge({ state, className }: { state: IncidentState; className?: string }) {
  const tone = STATE_TONE[state]
  return (
    <span
      data-testid="status-badge"
      data-state={state}
      className={cn(
        'inline-flex items-center gap-2 rounded-full px-3 py-1',
        'font-mono text-badge uppercase',
        'border transition-colors duration-status',
        TONE_BG[tone],
        TONE_BORDER[tone],
        'text-ink',
        className,
      )}
    >
      <span aria-hidden className={cn('h-2 w-2 rounded-full', TONE_DOT[tone])} />
      {STATE_LABEL[state]}
    </span>
  )
}

// ---------------------------------------------------------------------------------
// Pill
// ---------------------------------------------------------------------------------

/** A redaction badge, metadata tag, or the disclosure chip. Neutral unless given a tone. */
export function Pill({
  tone,
  className,
  children,
  testId,
}: {
  tone?: StatusTone
  className?: string
  children: ReactNode
  testId?: string
}) {
  return (
    <span
      data-testid={testId}
      className={cn(
        'inline-flex items-center rounded-sm border px-1.5 py-0.5 font-mono text-copy-secondary',
        tone ? cn(TONE_BG[tone], TONE_BORDER[tone], 'text-ink') : 'border-ink bg-secondary text-ink',
        className,
      )}
    >
      {children}
    </span>
  )
}

// ---------------------------------------------------------------------------------
// Sparkline
// ---------------------------------------------------------------------------------

interface SparklineProps {
  values: number[]
  tone: StatusTone
  /** Fixed y-domain. Required, because an auto-scaled axis is how a live chart lies. */
  domain: readonly [number, number]
  /** Renders at 40% opacity when the stream is down, so a stall cannot look like calm. */
  stale?: boolean
}

/**
 * A 2px monotone line with a gradient fill, no axes, no gridlines, no tooltip, no dots.
 *
 * Recharts' own animation is disabled: it re-runs on every data change and fights the 1 Hz
 * cadence. The tween that does happen is on the *data*, per §5.
 *
 * Sized in explicit pixels rather than through `ResponsiveContainer`, which rendered `null`
 * indefinitely in this build and left every tile with a blank strip under its number. The reasoning
 * and the reason no test caught it are in `hooks/useElementWidth.ts`.
 */
export function Sparkline({ values, tone, domain, stale = false }: SparklineProps) {
  const colour = STATUS_COLORS[tone]
  const data = values.slice(-SPARKLINE_WINDOW).map((value, index) => ({ index, value }))
  // Per instance, not per tone. All four golden-signal tiles share the run's tone, so a
  // tone-derived id put four elements with the same `id` in the document — invalid, and every
  // `url(#...)` reference would resolve to whichever one parsed first.
  //
  // The colons `useId` emits (`:r0:`) are stripped. `url(#:r0:)` does resolve in a presentation
  // attribute, but `querySelector('#:r0:')` throws a `SyntaxError` — React's own docs say the value
  // is not usable as a CSS selector. Leaving it raw would mean any later stylesheet rule or
  // selector-based assertion against the gradient fails in a way that looks like the gradient is
  // missing rather than unquotable.
  const gradientId = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const [wrapperRef, width] = useElementWidth<HTMLDivElement>()

  return (
    <div
      ref={wrapperRef}
      style={{ height: SPARKLINE_HEIGHT }}
      className={cn('w-full transition-opacity duration-status', stale && 'opacity-40 saturate-[0.4]')}
      data-testid="sparkline"
      data-stale={stale}
      // Whether the chart actually drew. A blank sparkline is indistinguishable from a flat line, so
      // this is asserted in the unit suite rather than left to the eye.
      data-rendered={width > 0}
    >
      {width > 0 && (
        <AreaChart
          width={width}
          height={SPARKLINE_HEIGHT}
          data={data}
          margin={{ top: 2, right: 0, bottom: 0, left: 0 }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colour} stopOpacity={0.15} />
              <stop offset="100%" stopColor={colour} stopOpacity={0} />
            </linearGradient>
          </defs>
          {/* Fixed domain, hidden axis: the scale must not move underneath the data. */}
          <YAxis domain={domain as [number, number]} hide />
          <Area
            type="monotone"
            dataKey="value"
            stroke={colour}
            strokeWidth={2}
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------
// MetricTile
// ---------------------------------------------------------------------------------

interface MetricTileProps {
  label: string
  value: number | null
  unit?: string
  decimals?: number
  tone: StatusTone
  history: number[]
  domain: readonly [number, number]
  stale?: boolean
}

/**
 * One golden-signal card.
 *
 * `tabular-nums` is mandatory: without it, `4,820` → `1,450` visibly jitters the layout ten times
 * a second and the whole dashboard reads as unstable. A null value renders an em dash in
 * `text-muted` rather than `NaN`.
 */
export function MetricTile({
  label,
  value,
  unit,
  decimals = 0,
  tone,
  history,
  domain,
  stale = false,
}: MetricTileProps) {
  const formatted =
    value === null || Number.isNaN(value)
      ? '—'
      : value.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })

  return (
    <Panel className={cn('p-4', TONE_BORDER[tone])} testId={`metric-${label}`}>
      <div className="mb-1 font-sans text-panel-title uppercase text-text-secondary">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span
          data-testid="metric-value"
          data-tone={tone}
          className={cn(
            'font-mono text-metric tabular-nums transition-colors duration-status',
            'text-ink',
          )}
        >
          {formatted}
        </span>
        {unit && <span className="font-mono text-metric-unit text-text-secondary">{unit}</span>}
      </div>
      <Sparkline values={history} tone={tone} domain={domain} stale={stale} />
    </Panel>
  )
}

// ---------------------------------------------------------------------------------
// IconButton
// ---------------------------------------------------------------------------------

/** A circular control with a 44×44 minimum hit area, per the §11 touch-target rule. */
export function IconButton({
  label,
  onClick,
  disabled,
  className,
  children,
}: {
  label: string
  onClick?: () => void
  disabled?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-2 rounded-full',
        'border border-ink bg-secondary px-4 font-mono text-badge uppercase text-ink',
        'transition-colors duration-status hover:border-strong',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard',
        'disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------------
// Terminal
// ---------------------------------------------------------------------------------

interface TerminalProps {
  /** Rendered inside the scroll region. Callers own their line markup. */
  children: ReactNode
  /** Shown instead of `children` when there is nothing to display. */
  emptyCopy: string
  /** Whether to show `emptyCopy`. Explicit, because "no lines" is caller-specific. */
  isEmpty: boolean
  /**
   * Changes to this value trigger an auto-scroll. Pass the line count — a new frame means a new
   * count, and a scroll driven by `children` identity would fire on every parent render.
   */
  scrollKey: number
  maxHeightClass?: string
  testId?: string
  /** Names the scroll region for the keyboard focus stop it creates. */
  label: string
}

/**
 * The monospace scroll region — the log tail and the execution terminal both sit in one of these.
 *
 * Two behaviours the callers must not have to reimplement, and did not have when they rendered
 * their own `<div className="overflow-y-auto">`: it **auto-scrolls to the newest line**, and it
 * **pauses while hovered**. The pause is the part that matters. Worker output arrives as a burst and
 * the log tail streams at 1 Hz, so a viewer who moves the cursor to read a line would otherwise have
 * it yanked out from under them by the next frame.
 *
 * It takes children rather than a `lines` array because the two callers render genuinely different
 * markup — one wraps redaction tokens in pills, the other wraps an `s3://` URI in an anchor — and a
 * primitive that tried to model both would end up as a switch on caller identity. What is shared is
 * the scroll behaviour, so that is what this owns.
 */
export function Terminal({
  children,
  emptyCopy,
  isEmpty,
  scrollKey,
  maxHeightClass = 'max-h-48',
  testId,
  label,
}: TerminalProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    if (paused || !scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [scrollKey, paused])

  return (
    <div
      ref={scrollRef}
      data-testid={testId}
      data-paused={paused}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      // A labelled group, and deliberately **not** focusable.
      //
      // Not `role="log"`, because that carries an implicit `aria-live="polite"` and §11's one
      // explicit exception to the live-region rule is this content — a screen reader reciting ten
      // log lines a second is unusable.
      //
      // And not `tabIndex={0}` either, though it was at first. Making a scroll region focusable is a
      // reasonable affordance in general, but here it inserted the log tail into the tab order
      // *before* the authorize button on desktop, where the agent panel is the third column. §11
      // requires the HITL controls to come first, and that requirement is load-bearing: it is the
      // one control that changes anything. Nothing inside this region is interactive, so the trade
      // is two lost scroll stops against burying the gate — `tests/e2e/accessibility.spec.ts`
      // asserts the resulting order.
      role="group"
      aria-label={label}
      className={cn('overflow-y-auto rounded-md bg-raised p-3 font-mono text-log text-console', maxHeightClass)}
    >
      {isEmpty ? <p className="font-sans text-body text-console">{emptyCopy}</p> : children}
    </div>
  )
}

// ---------------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------------

/**
 * Cold-start placeholder. The nine containers take time to become healthy, and a blank screen
 * during that window reads as a broken deployment rather than a starting one.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cn('relative overflow-hidden rounded-md bg-surface-2', className)} data-testid="skeleton">
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/5 to-transparent" />
    </div>
  )
}
