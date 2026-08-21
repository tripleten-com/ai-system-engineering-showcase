/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        tailwind.config.js
 * Purpose:          Projects the design tokens into Tailwind, so a component can only reach a
 *                   design value through a class name.
 * Interacts With:   src/theme/tokens.ts (the source), every component
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Design Systems, Tailwind Theme Extension
 * Tools:            Tailwind CSS 3
 *
 * Values are duplicated here from `src/theme/tokens.ts` rather than imported: this file is loaded
 * by PostCSS in a plain-Node context that cannot resolve a `.ts` module, and adding a build step
 * to a config file is worse than the duplication. `tests/unit/theme.test.ts` asserts the two stay
 * in agreement, so the duplication cannot drift silently.
 */

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        page: '#FFFFFF',
        secondary: '#F2F1EE',
        ink: '#1A1A1A',
        raised: '#2A2A2A',
        accent: '#FF976B',
        console: '#F2F1EE',
        // Machine output inside a console body, and nothing else. See tokens.ts.
        'console-output': '#FFFFFF',
        // Surfaces — §2. Named by role, not by shade, so a panel cannot accidentally sit on the
        // page ground.
        'surface-0': '#FFFFFF',
        'surface-1': '#F2F1EE',
        'surface-2': '#2A2A2A',

        // Status — §1. One meaning each.
        healthy: '#3AA65E',
        alarm: '#ED6F68',
        pending: '#FFA800',
        active: '#3F96F3',
        guard: '#8754FD',

        // Text — §4.
        'text-primary': '#1A1A1A',
        'text-secondary': '#1A1A1A',
        'text-muted': '#1A1A1A',
        'text-console': '#F2F1EE',
      },
      borderColor: {
        subtle: '#F2F1EE',
        strong: '#1A1A1A',
      },
      borderRadius: {
        sm: '4px',
        md: '8px',
        lg: '12px',
        full: '9999px',
      },
      fontFamily: {
        // §4 — split by origin of the text. Anything the machine emitted is mono; anything a
        // human wrote is sans. This is the rule that makes the UI read as a real console.
        display: ['Manrope Variable', 'Manrope', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
        sans: ['Inter Variable', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // Streamed console output only. A system face rather than a bundled one, deliberately:
        // this is the terminal look, and Courier New is the face a terminal has on the machines
        // this demo is shown on. It needs no webfont request either way.
        console: ['"Courier New"', 'Courier', 'monospace'],
      },
      height: {
        'console-mobile': '360px',
        'console-tablet': '420px',
        'console-desktop': '480px',
        'metric-card': '132px',
        // Height only — the wordmark's width follows from its own aspect ratio. See tokens.ts.
        'brand-logo': '40px',
        // Fixed, not floored, so two consoles side by side align. See tokens.ts.
        'console-footer': '96px',
      },
      minHeight: {
        // The console heights appear here as well as in `height`: an expanded console swaps its
        // fixed height for the same value as a floor, and `min-h-console-*` has to exist as a real
        // utility for that to do anything at all. Same for the footer.
        'console-mobile': '360px',
        'console-tablet': '420px',
        'console-desktop': '480px',
        'console-footer': '96px',
      },
      backgroundImage: {
        // The TripleTen page-background wash, sampled from tripleten.com. See tokens.ts for why the
        // blue end is lightened rather than the full-strength brand blue.
        'scenario-trigger': 'linear-gradient(135deg, #FFD6C5 0%, #BFD6F7 100%)',
      },
      boxShadow: {
        offset: '4px 4px 0 0 #1A1A1A',
        'offset-lift': '6px 6px 0 0 #1A1A1A',
        'offset-press': '1px 1px 0 0 #1A1A1A',
      },
      maxWidth: {
        showcase: '1600px',
      },
      width: {
        'postmortem-drawer': '480px',
      },
      letterSpacing: {
        eyebrow: '0.08em',
      },
      fontSize: {
        // §4 — the type scale, as [size, {lineHeight, letterSpacing, fontWeight}].
        brand: ['20px', { lineHeight: '1.2', letterSpacing: '0.02em', fontWeight: '600' }],
        'panel-title': ['13px', { lineHeight: '1.3', letterSpacing: '0.08em', fontWeight: '600' }],
        metric: ['32px', { lineHeight: '1.1', letterSpacing: '-0.01em', fontWeight: '700' }],
        'metric-unit': ['12px', { lineHeight: '1.3', fontWeight: '500' }],
        body: ['14px', { lineHeight: '1.5', fontWeight: '400' }],
        secondary: ['12px', { lineHeight: '1.4', fontWeight: '400' }],
        'copy-secondary': ['12px', { lineHeight: '1.4', fontWeight: '400' }],
        log: ['12px', { lineHeight: '1.5', fontWeight: '400' }],
        // Streamed console output. Tighter leading than `log` so consecutive lines read as one
        // continuous transcript rather than as separately spaced paragraphs.
        'console-line': ['12px', { lineHeight: '1.35', fontWeight: '400' }],
        badge: ['12px', { lineHeight: '1.2', letterSpacing: '0.06em', fontWeight: '600' }],
        eyebrow: ['12px', { lineHeight: '1.2', letterSpacing: '0.08em', fontWeight: '600' }],
        hero: ['30px', { lineHeight: '1.1', letterSpacing: '-0.01em', fontWeight: '600' }],
        'hero-desktop': ['48px', { lineHeight: '1.1', letterSpacing: '-0.01em', fontWeight: '600' }],
        'hero-body': ['18px', { lineHeight: '1.5', fontWeight: '400' }],
      },
      transitionDuration: {
        metric: '900ms',
        status: '200ms',
        'panel-enter': '250ms',
        'log-append': '150ms',
        'redaction-fade': '250ms',
        'banner-enter': '300ms',
      },
      keyframes: {
        // §2 — emphasis uses glow, not shadow. A box-shadow on #0B0F19 is invisible.
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 0 1px currentColor, 0 0 24px -6px currentColor' },
          '50%': { boxShadow: '0 0 0 1px currentColor, 0 0 32px -2px currentColor' },
        },
        'panel-enter': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        // §5 — the HITL pulse glows and never scales. A resizing button is a moving target.
        'hitl-pulse': 'pulse-glow 1600ms ease-in-out infinite',
        'panel-enter': 'panel-enter 250ms ease-out',
        'fade-in': 'fade-in 150ms linear',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
