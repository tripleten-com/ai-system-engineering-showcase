/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        Footer.tsx
 * Purpose:          The single call to action a visitor takes after watching the pipeline run.
 * Interacts With:   App.tsx, components/Header.tsx (which reuses REPOSITORY_URL)
 *
 * Curriculum Project: Cross-cutting — Marketing Delivery
 * Skills:           Conversion Affordances, Accessibility, Configuration
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * One destination, no summary copy. The page above it has just spent a full incident demonstrating
 * what it does; restating that in two lines of footer prose asked the visitor to read a description
 * of something they had already watched, and a second CTA next to the real one splits the click.
 * The repository is still one click away — the header carries that link, and `REPOSITORY_URL` is
 * still exported from here because the header imports it.
 *
 * **Both URLs are configurable.** `VITE_CURRICULUM_URL` and `VITE_REPOSITORY_URL` override the
 * defaults at build time, because a marketing page's destination is a campaign decision that
 * changes more often than the code does — and a hardcoded landing page is how a deployed demo ends
 * up pointing at a dead link.
 */

import { ArrowUpRight } from 'lucide-react'

import { cn } from '../lib/cn'

/**
 * `||`, not `??`, and the difference is load-bearing here.
 *
 * Compose passes these as build args with an empty-string default (`${VAR:-}`), because a build arg
 * that is simply absent is awkward to express per-service. `??` falls back only on null/undefined,
 * so an unset variable arrived as `""` and *overrode* the default — producing `href=""`. `||`
 * treats an empty string as "not configured", which is what it means.
 */

/**
 * Where the CTA sends a visitor.
 *
 * The programme page this demo is a showcase for. It replaces the domain root that stood here as a
 * deliberately safe placeholder — the root was chosen because an *invented* campaign path would be a
 * plausible-looking 404, and this one is not invented: it was checked and resolves 200.
 * `VITE_CURRICULUM_URL` still overrides it for a campaign-specific landing page.
 */
export const CURRICULUM_URL =
  import.meta.env.VITE_CURRICULUM_URL || 'https://tripleten.com/systems-engineer/'

/** Consumed by `Header.tsx`, which is where the repository link lives. */
export const REPOSITORY_URL =
  import.meta.env.VITE_REPOSITORY_URL || 'https://github.com/tripleten-com/ai-system-engineering-showcase'

const LINK_BASE =
  'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-full px-5 font-mono text-badge uppercase ' +
  'transition-colors duration-status ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard'

export function Footer() {
  return (
    <footer
      data-testid="footer"
      className="flex justify-center border-t border-subtle pt-6"
    >
      <nav aria-label="Next steps">
        <a
          href={CURRICULUM_URL}
          target="_blank"
          rel="noreferrer"
          data-testid="cta-curriculum"
          className={cn(LINK_BASE, 'border border-guard bg-guard/15 text-ink hover:bg-guard/25')}
        >
          Explore the programme
          <ArrowUpRight aria-hidden className="h-4 w-4 shrink-0" />
        </a>
      </nav>
    </footer>
  )
}
