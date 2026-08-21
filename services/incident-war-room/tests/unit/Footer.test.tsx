/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        Footer.test.tsx
 * Purpose:          Tests the single call to action — that it exists, is configurable, opens
 *                   safely, meets the touch-target minimum, and is not surrounded by summary copy.
 * Interacts With:   Footer component
 *
 * Curriculum Project: Cross-cutting — Marketing Delivery
 * Skills:           React Component Testing, Accessibility
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import { CURRICULUM_URL, Footer, REPOSITORY_URL } from '../../src/components/Footer'

describe('Footer', () => {
  it('offers the one next step', () => {
    render(<Footer />)

    expect(screen.getByTestId('cta-curriculum')).toHaveAttribute('href', CURRICULUM_URL)
    expect(screen.getByTestId('cta-curriculum')).toHaveTextContent('Explore the programme')
  })

  it('no longer competes with a second call to action', () => {
    // Two CTAs side by side split the click, and the repository is still one click away from the
    // header. The footer's job is now the single conversion affordance.
    render(<Footer />)

    expect(screen.queryByTestId('cta-repository')).not.toBeInTheDocument()
    expect(screen.getByTestId('footer')).not.toHaveTextContent(/read the source/i)
  })

  it('opens the external destination without handing it the opener', () => {
    // `rel="noreferrer"` implies `noopener`. Without it a target-blank link gives the destination a
    // handle on this window, which is a real vulnerability and free to avoid.
    render(<Footer />)

    expect(screen.getByTestId('cta-curriculum')).toHaveAttribute('target', '_blank')
    expect(screen.getByTestId('cta-curriculum')).toHaveAttribute('rel', 'noreferrer')
  })

  it('meets the 44px touch-target minimum', () => {
    render(<Footer />)

    expect(screen.getByTestId('cta-curriculum').className).toContain('min-h-[44px]')
  })

  it('groups the call to action as a labelled navigation region', () => {
    render(<Footer />)

    expect(screen.getByRole('navigation', { name: /next steps/i })).toBeInTheDocument()
  })

  it('defaults to destinations that resolve rather than to a guessed campaign path', () => {
    // A specific landing-page URL invented in code is a plausible-looking 404. The defaults are a
    // domain root and the repository; `VITE_CURRICULUM_URL` points them at the real campaign.
    expect(CURRICULUM_URL).toMatch(/^https:\/\//)
    expect(REPOSITORY_URL).toMatch(/^https:\/\/github\.com\//)
  })

  it('never renders an empty destination', () => {
    // The trap this guards. Compose passes these as build args with an empty-string default
    // (`${VAR:-}`), and `??` falls back only on null/undefined — so an unset variable arrived as
    // `""` and *overrode* the default, rendering `href=""`. Every URL setting that has a real
    // default uses `||` for exactly this reason.
    render(<Footer />)

    const href = screen.getByTestId('cta-curriculum').getAttribute('href')
    expect(href).toBeTruthy()
    expect(href).not.toBe('')
  })

  it('carries no summary copy about what the demo showed', () => {
    // The page above has just spent a full incident demonstrating this. Restating it in the footer
    // asked the visitor to read a description of something they had already watched.
    render(<Footer />)

    const footer = screen.getByTestId('footer')
    expect(footer).not.toHaveTextContent(/human decision before any repair runs/i)
    expect(footer).not.toHaveTextContent(/local cloud services/i)
    expect(footer).toHaveTextContent('Explore the programme')
  })
})
