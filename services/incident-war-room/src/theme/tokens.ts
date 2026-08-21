/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        theme/tokens.ts
 * Purpose:          The single source for every colour, scale, and duration in the UI.
 * Interacts With:   tailwind.config.js (consumes this), every component (via Tailwind classes)
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Design Tokens, Design System Implementation
 * Tools:            TypeScript, Tailwind CSS
 *
 * Feature components never hardcode a hex value, radius, duration, or font size. They cannot:
 * everything below is exported into the Tailwind theme, so the only way to reach a colour is
 * through a class name, and the only way to add one is to add it here.
 *
 * `STATUS_COLORS` is the load-bearing table. Colour in this UI is a status system and never
 * decoration — so a status colour used for a non-status purpose is a defect, and any status
 * surface must also carry its state as text (spa-design-guidelines.md §1, and the a11y rule in
 * §11 that says never colour alone).
 */

import type { IncidentState } from '../types/contracts.gen'

/** §1 — the five status colours, each meaning one thing. */
export const FOUNDATION_COLORS = {
  page: '#FFFFFF',
  secondary: '#F2F1EE',
  ink: '#1A1A1A',
  raised: '#2A2A2A',
  accent: '#FF976B',
  console: '#F2F1EE',
  /**
   * Machine output inside a console body, and nothing else.
   *
   * A separate token from `console` because the two answer different questions. `console` is
   * "readable text on a dark surface" and is used by chrome that happens to sit on one;
   * `console-output` means "a machine emitted this", and it is only ever applied inside a
   * `ConsoleFrame` body. Using it for a label would make the label look like a log line.
   *
   * White rather than the phosphor green it started as: green reads as a *status* on a surface where
   * every other colour is one, and a whole console of it made healthy and alarming output look
   * equally emphatic. White is neutral, so the level colours that override it — `pending` for a
   * warning, `alarm` for an error — are the only things in the body that mean anything.
   */
  'console-output': '#FFFFFF',
} as const

/**
 * The TripleTen page-background wash, sampled from tripleten.com.
 *
 * `#FFD6C5` is the brand peach used in its own gradient assets; the blue end is `#1863DC` — the
 * brand blue on that page — lightened until ink clears the 4.5:1 body-text floor on top of it.
 * Full-strength `#1863DC` reaches only 3.2:1 against ink, so a vivid orange-to-blue fill could not
 * carry the button labels. The soft wash is also what the site's own backgrounds actually look
 * like, so this is the closer match as well as the legible one.
 */
export const BRAND_GRADIENT = {
  from: '#FFD6C5',
  to: '#BFD6F7',
} as const

/** Projected into Tailwind as `bg-scenario-trigger`. Feature components never write the stops. */
export const GRADIENTS = {
  'scenario-trigger': `linear-gradient(135deg, ${BRAND_GRADIENT.from} 0%, ${BRAND_GRADIENT.to} 100%)`,
} as const

export const STATUS_COLORS = {
  healthy: '#3AA65E',
  alarm: '#ED6F68',
  pending: '#FFA800',
  active: '#3F96F3',
  guard: '#8754FD',
} as const

export type StatusTone = keyof typeof STATUS_COLORS

/** §2 — surfaces separate planes with lighter fills and hairlines, never drop shadows. */
export const SURFACES = {
  'surface-0': FOUNDATION_COLORS.page,
  'surface-1': FOUNDATION_COLORS.secondary,
  'surface-2': FOUNDATION_COLORS.raised,
} as const

export const BORDERS = {
  subtle: FOUNDATION_COLORS.secondary,
  strong: FOUNDATION_COLORS.ink,
} as const

/**
 * §4 — text roles for the light shell and raised console.
 *
 * Shell copy uses ink on pale surfaces, while console copy uses the secondary surface color on
 * raised dark frames. `muted` remains a guarded legacy alias and feature components may not use it.
 */
export const TEXT = {
  primary: FOUNDATION_COLORS.ink,
  secondary: FOUNDATION_COLORS.ink,
  muted: FOUNDATION_COLORS.ink,
  console: FOUNDATION_COLORS.console,
} as const

/**
 * The one frame-height scale, shared by all four consoles.
 *
 * Uniform on purpose: the four sit in a 2×2 grid, and a grid whose cells are different heights for no
 * reason a reader can name looks like a bug rather than like a distinction. One scale also means the
 * output areas match, which is the thing an eye actually compares.
 *
 * The desktop value is 480px rather than the 420px the three workflow consoles used to have. The
 * worker console previously auto-grew from a 480px floor, and shrinking it to 420 to achieve
 * uniformity would have undone that — so uniformity was reached by raising the other three instead.
 * Every console also exists as a `minHeight`, because an expanded disclosure swaps the height for the
 * same value as a floor.
 */
export const CONSOLE_HEIGHTS = {
  'console-mobile': '360px',
  'console-tablet': '420px',
  'console-desktop': '480px',
} as const

/**
 * Fixed component heights outside the streamed console frames.
 *
 * `brand-logo` is a *height* only; the wordmark's width follows from its own 87.546:20 aspect ratio,
 * so constraining both would distort the mark. 40px fills the header band beside the two 44px
 * controls opposite it without becoming the loudest thing on a page whose job is the demo below.
 */
export const COMPONENT_HEIGHTS = {
  'metric-card': '132px',
  'brand-logo': '40px',
  /**
   * The console footer, where it is fixed rather than floored.
   *
   * A floor lets two consoles side by side settle at different heights whenever their footer content
   * differs, which reads as a misalignment rather than as a difference. The decision pair asks for
   * one line across both, so their footers take this as a height and the bodies absorb the slack.
   */
  'console-footer': '96px',
} as const

/**
 * Fixed widths that are a layout decision rather than a content one.
 *
 * The postmortem drawer is 480px because it holds a JSON report read as prose: narrower truncates
 * the execution-log lines, wider starts to cover the war room it is reporting on. Below 768px it
 * ignores this and becomes a full-screen sheet, because a 480px drawer on a 375px phone is a
 * horizontal scrollbar.
 */
export const WIDTHS = {
  'postmortem-drawer': '480px',
} as const

export const LAYOUT = {
  showcase: '1600px',
  eyebrowTracking: '0.08em',
  hero: '30px',
  heroDesktop: '48px',
  heroBody: '18px',
} as const

/**
 * Every name in the type scale.
 *
 * This exists for `lib/cn.ts`, and the reason is a bug that shipped: `tailwind-merge` groups classes
 * by prefix, so it read our custom `text-log` (a *size*) and `text-console-output` (a *colour*) as
 * the same `text-` group and dropped the first. `cn('… text-log', 'text-console-output')` therefore
 * silently lost the font size and the retrieval console rendered at the inherited 16px while its
 * neighbours rendered at 12px — visible as "the font size doesn't match" and invisible in the
 * source. Naming the scale lets `cn` tell twMerge which `text-*` classes are sizes.
 *
 * `theme.test.ts` asserts this list is exactly the keys of the Tailwind `fontSize` scale, so a size
 * added to the config and not here re-opens the same hole rather than merely being unmergeable.
 */
export const FONT_SIZE_NAMES = [
  'brand',
  'panel-title',
  'metric',
  'metric-unit',
  'body',
  'secondary',
  'copy-secondary',
  'log',
  'console-line',
  'badge',
  'eyebrow',
  'hero',
  'hero-desktop',
  'hero-body',
] as const

/**
 * The face streamed console output is set in.
 *
 * A system stack rather than a bundled Fontsource package, and the only one in the UI that is. The
 * three bundled faces are brand typography and have to render identically everywhere; this one is
 * *costume* — it is what a terminal looks like — and Courier New is present on every platform this
 * demo is shown on. Bundling a webfont to achieve "looks like a terminal" would add a payload for
 * no gain, and it still needs no network request either way.
 */
export const CONSOLE_FONT_STACK = ['"Courier New"', 'Courier', 'monospace'] as const

/**
 * Hard offset shadows for the scenario launcher.
 *
 * Offset rather than blurred, because the launcher buttons have to read as *pressable* on a white
 * editorial page where nothing else does. A soft drop shadow on `#FFFFFF` is nearly invisible; a
 * hard ink offset is unmistakably a button, and shrinking it on `:active` is the press.
 */
export const SHADOWS = {
  offset: `4px 4px 0 0 ${FOUNDATION_COLORS.ink}`,
  'offset-lift': `6px 6px 0 0 ${FOUNDATION_COLORS.ink}`,
  'offset-press': `1px 1px 0 0 ${FOUNDATION_COLORS.ink}`,
} as const

/** §2 — tighter than consumer soft UI: instrumentation, not a wellness app. */
export const RADII = {
  sm: '4px',
  md: '8px',
  lg: '12px',
  full: '9999px',
} as const

/** §5 — motion. The metric tween sits *under* the 1 Hz data cadence so the line reads continuous. */
export const DURATIONS = {
  metric: '900ms',
  status: '200ms',
  panelEnter: '250ms',
  reasoningStagger: '120ms',
  logAppend: '150ms',
  redactionHold: '400ms',
  redactionFade: '250ms',
  hitlPulse: '1600ms',
  bannerEnter: '300ms',
} as const

/**
 * §1 — run state to status tone. The single source of truth for this mapping.
 *
 * The two entries worth reading twice are the Scenario 4 ones. `EXPLOIT_INTERCEPTED` and
 * `SECURITY_CONTAINED` are `guard`, not `alarm`: a guardrail holding is a success, and painting
 * them red would tell the exact opposite of the story the scenario exists to tell.
 */
export const STATE_TONE: Record<IncidentState, StatusTone> = {
  HEALTHY: 'healthy',
  CRITICAL_OUTAGE: 'alarm',
  EXPLOIT_INTERCEPTED: 'guard',
  AWAITING_APPROVAL: 'pending',
  EXECUTING: 'active',
  RECOVERING: 'active',
  REJECTED: 'pending',
  FAILED: 'alarm',
  SECURITY_CONTAINED: 'guard',
}

/**
 * The badge text per state. Contractual: the E2E specs read these off the DOM.
 *
 * Scenario 4 reads `SECURITY EVENT (DEGRADED)` rather than `CRITICAL OUTAGE`, which is the
 * distinction ui-wireframe-and-ux.md §3 insists "must not be subtle".
 */
export const STATE_LABEL: Record<IncidentState, string> = {
  HEALTHY: 'System healthy',
  CRITICAL_OUTAGE: 'Service outage detected',
  EXPLOIT_INTERCEPTED: 'Unsafe request blocked',
  // "human", not "your". A visitor watching a recorded demo, or a reviewer reading a screenshot,
  // is not the approver — and the claim the gate makes is about a person being required at all,
  // not about this particular reader.
  AWAITING_APPROVAL: 'Waiting for human approval',
  EXECUTING: 'Approved fix is running',
  RECOVERING: 'Service is recovering',
  REJECTED: 'Fix not approved',
  FAILED: 'Fix failed',
  SECURITY_CONTAINED: 'Security threat contained',
}

/**
 * §6 — fixed y-axis domains per metric. The single most common way a live dashboard lies is
 * auto-scaling: it makes a 4,820ms spike look identical to a 48ms baseline because the axis
 * rescaled underneath it. Every sparkline takes its domain from here.
 */
export const CHART_DOMAINS = {
  latency_p99_ms: [0, 5000],
  latency_p95_ms: [0, 3000],
  latency_p50_ms: [0, 100],
  http_5xx_error_rate_pct: [0, 50],
  requests_per_sec: [0, 200],
  db_pool_utilization_pct: [0, 100],
  redis_memory_utilization_pct: [0, 100],
  cache_hit_ratio_pct: [0, 100],
  sqs_active_queue_depth: [0, 1600],
} as const satisfies Record<string, readonly [number, number]>

/** §6 — the last 60 samples: long enough to show jitter, short enough that a spike dominates. */
export const SPARKLINE_WINDOW = 60

/**
 * Sparkline height in pixels.
 *
 * A number rather than a Tailwind class because the chart needs it as an explicit `height` prop —
 * see `hooks/useElementWidth.ts` for why the charts are sized in pixels rather than percentages.
 * One constant drives both the wrapper's box and the chart's, so they cannot disagree.
 */
export const SPARKLINE_HEIGHT = 40
