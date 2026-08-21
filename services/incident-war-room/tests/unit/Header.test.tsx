/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        Header.test.tsx
 * Purpose:          Tests the two strips that must never be confused — cold start and a lost
 *                   stream — plus the disclosure panel.
 * Interacts With:   Header component
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           React Component Testing, Degraded-Mode UX, Accessibility
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Header } from '../../src/components/Header'

describe('Header', () => {
  it('leaves current-state presentation to the hero so App has one status badge', () => {
    render(<Header state="CRITICAL_OUTAGE" connection="open" reconnectAttempt={0} />)

    expect(screen.queryByTestId('status-badge')).not.toBeInTheDocument()
  })

  it('shows the cold-start message while the stream is first opening', () => {
    // Not the disconnected strip. Reporting "DISCONNECTED — RECONNECTING (attempt 0)" on first
    // paint claims a failure that has not happened, and the zero is the giveaway.
    render(<Header state="HEALTHY" connection="connecting" reconnectAttempt={0} />)

    expect(screen.getByTestId('stream-connecting')).toHaveTextContent(/Connecting to telemetry stream/i)
    expect(screen.queryByTestId('stream-disconnected')).not.toBeInTheDocument()
  })

  it('shows the disconnected strip with the attempt count once a connection is lost', () => {
    render(<Header state="HEALTHY" connection="reconnecting" reconnectAttempt={3} />)

    const strip = screen.getByTestId('stream-disconnected')
    expect(strip).toHaveTextContent(/TELEMETRY STREAM DISCONNECTED/i)
    expect(strip).toHaveTextContent('attempt 3')
    expect(screen.queryByTestId('stream-connecting')).not.toBeInTheDocument()
  })

  it('shows neither strip while the stream is healthy', () => {
    render(<Header state="HEALTHY" connection="open" reconnectAttempt={0} />)

    expect(screen.queryByTestId('stream-connecting')).not.toBeInTheDocument()
    expect(screen.queryByTestId('stream-disconnected')).not.toBeInTheDocument()
  })

  it('keeps the disclosure chip neutral — it states a fact, not a status', () => {
    render(<Header state="CRITICAL_OUTAGE" connection="open" reconnectAttempt={0} />)

    const chip = screen.getByTestId('disclosure-toggle')
    expect(chip).toHaveTextContent('Project info')
    for (const status of ['text-alarm', 'text-pending', 'text-healthy', 'text-active']) {
      expect(chip.className).not.toContain(status)
    }
  })

  it('separates what is simulated from what is real', async () => {
    render(<Header state="HEALTHY" connection="open" reconnectAttempt={0} />)
    expect(screen.queryByTestId('disclosure-panel')).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('disclosure-toggle'))

    const panel = screen.getByTestId('disclosure-panel')
    expect(panel).toHaveTextContent(/Simulated/)
    expect(panel).toHaveTextContent(/Real/)
    // The claims that must stay on the "real" side of the line.
    expect(panel).toHaveTextContent(/pgvector/)
    expect(panel).toHaveTextContent(/checkpointed interrupt/)
    expect(panel).toHaveTextContent(/Pydantic tool firewall/)
  })
})

describe('brand identity', () => {
  it('leads with the TripleTen wordmark rather than the programme name', () => {
    render(<Header state="HEALTHY" connection="open" reconnectAttempt={0} />)

    const heading = screen.getByRole('heading', { level: 1 })
    const logo = screen.getByTestId('brand-logo')

    expect(heading).toContainElement(logo)
    // The official vector, not type set to look like it: `alt` is the brand name and the file is
    // the asset TripleTen publishes.
    expect(logo.tagName).toBe('IMG')
    expect(logo).toHaveAccessibleName('TripleTen')
    expect(logo.getAttribute('src')).toMatch(/tripleten-wordmark\.svg/)
    // The programme and its "showcase" qualifier moved to the hero eyebrow, where they read as a
    // subtitle to the page rather than as a second half of the brand's own name.
    expect(heading).not.toHaveTextContent(/AI Systems Engineering/)
  })

  it('sizes the wordmark by height so its aspect ratio is never distorted', () => {
    // Pinning both axes is how a logo ends up stretched. The mark is 87.546:20; height alone plus
    // `w-auto` keeps that exact.
    render(<Header state="HEALTHY" connection="open" reconnectAttempt={0} />)

    const logo = screen.getByTestId('brand-logo')
    expect(logo.className).toContain('h-brand-logo')
    expect(logo.className).toContain('w-auto')
    expect(logo.className).not.toMatch(/(?:^|\s)w-(?!auto)/)
  })

  it('still names the page for a screen reader', () => {
    // A wordmark alone leaves the h1 saying only "tripleten", which tells an assistive-technology
    // user the brand but not what they have landed on.
    render(<Header state="HEALTHY" connection="open" reconnectAttempt={0} />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveAccessibleName(/Incident War Room/)
  })
})
