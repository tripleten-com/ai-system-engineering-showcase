/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        hooks/useMediaQuery.ts
 * Purpose:          Subscribes to a CSS media query from React, so a breakpoint can decide which
 *                   component *exists* rather than only which one is visible.
 * Interacts With:   App.tsx (the mobile HITL rule)
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Responsive Design, Effect Lifecycle, Accessibility
 * Tools:            React 18, TypeScript
 *
 * Almost every responsive decision in this UI is pure CSS, as it should be. This hook exists for
 * the one that cannot be: the HITL controls.
 *
 * Rendering both the inline gate and the sticky bar and hiding one with `md:hidden` would put two
 * elements with the same role and the same test id in the DOM at once. `display: none` does keep
 * the hidden one out of the tab order, so it would not be an accessibility bug — but it would make
 * every Playwright `getByTestId('authorize-remediation')` ambiguous, and an ambiguous selector on
 * the one control the whole demo depends on is a bad trade for saving a listener.
 */

import { useEffect, useState } from 'react'

/**
 * Returns whether the query currently matches, and re-renders when that changes.
 *
 * Defaults to `false` when `matchMedia` is unavailable, which keeps the desktop layout as the
 * fallback: a wide layout on a narrow screen is awkward, where the reverse would strand the HITL
 * controls in a sticky bar that the desktop viewport has no room for.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false,
  )

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const list = window.matchMedia(query)
    // Re-read on subscribe: the query may have changed between the initial state and this effect,
    // and on a device rotation that gap is exactly where a stale value would be visible.
    setMatches(list.matches)

    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])

  return matches
}

/** Below Tailwind's `md`. The width at which the HITL controls move to the sticky bottom bar. */
export const BELOW_MD = '(max-width: 767px)'
