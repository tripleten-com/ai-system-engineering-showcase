/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        RagInspectorView.test.tsx
 * Purpose:          Unit tests for score formatting, the provenance label, and the live
 *                   retrieval probe.
 * Interacts With:   RagInspectorView component, services/api.ts
 *
 * Curriculum Project: Project 2 — Hybrid RAG & Retrieval Architecture
 * Skills:           React Component Testing, RAG Inspector UI
 * Tools:            Vitest, React Testing Library
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { RagInspectorView } from '../../src/components/RagInspectorView'
import * as api from '../../src/services/api'
import { RunbookId } from '../../src/types/contracts.gen'
import { RB_104_MATCH, ragEntry } from '../fixtures'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RagInspectorView', () => {
  it('renders the runbook id, title, score, rank and provenance', () => {
    render(<RagInspectorView matches={[ragEntry()]} />)

    const match = screen.getByTestId('rag-match')
    expect(match.dataset.runbookId).toBe(RunbookId.RB_104)
    expect(match).toHaveTextContent('PostgreSQL Connection Pool Drain & Recycle')
    expect(screen.getByTestId('rag-source')).toHaveTextContent('pgvector (cosine) + FTS, fused via RRF')
  })

  it('prints the cosine similarity to four decimals', () => {
    // Not rounded to 0.94 for tidiness: the digits are what show the score moving when the query
    // does, which is the claim the panel exists to support.
    render(<RagInspectorView matches={[ragEntry()]} />)

    expect(screen.getByTestId('cosine-similarity').textContent).toBe('0.9412')
  })

  it('shows the standby copy before any retrieval', () => {
    render(<RagInspectorView matches={[]} />)

    expect(screen.getByText(/A recovery guide will appear here when the incident needs one\./)).toBeInTheDocument()
  })

  it('renders the live guide in the console face, colour, and size', () => {
    render(<RagInspectorView matches={[ragEntry()]} />)

    const match = screen.getByTestId('rag-match')
    expect(match.className).toContain('font-console')
    expect(match.className).toContain('text-console-output')
    // The size is the regression. This element composes its classes through `cn`, and twMerge read
    // the custom `text-console-line` size and `text-console-output` colour as one `text-` group and
    // dropped the size — so this console rendered at the inherited 16px beside neighbours at 12px.
    // `lib/cn.ts` now declares the type scale; losing that declaration reopens the hole here.
    expect(match.className).toContain('text-console-line')
  })

  it('runs the visitor query against the real retrieval endpoint', async () => {
    const searchRunbooks = vi
      .spyOn(api, 'searchRunbooks')
      .mockResolvedValue({ query: 'redis keys expiring', results: [RB_104_MATCH] })

    render(<RagInspectorView matches={[]} />)

    await userEvent.type(screen.getByTestId('rag-probe-input'), 'redis keys expiring')
    await userEvent.click(screen.getByTestId('rag-probe-submit'))

    // `waitFor` first, then the spy: the probe's state settles a microtask after the click, and
    // asserting before that produces a React `act` warning for a render the test never observed.
    await waitFor(() => expect(screen.getByTestId('rag-probe-results')).toBeInTheDocument())
    expect(searchRunbooks).toHaveBeenCalledWith('redis keys expiring')
  })

  it('keeps the probe result separate from the incident match', async () => {
    // Two different claims. Letting a visitor's query overwrite the incident's match would make
    // the panel ambiguous about which retrieval it is showing.
    vi.spyOn(api, 'searchRunbooks').mockResolvedValue({ query: 'cache', results: [RB_104_MATCH] })

    render(<RagInspectorView matches={[ragEntry()]} />)

    await userEvent.type(screen.getByTestId('rag-probe-input'), 'cache')
    await userEvent.click(screen.getByTestId('rag-probe-submit'))

    await waitFor(() => expect(screen.getAllByTestId('rag-match')).toHaveLength(2))
  })

  it('refuses to submit an empty query', async () => {
    const searchRunbooks = vi.spyOn(api, 'searchRunbooks')

    render(<RagInspectorView matches={[]} />)

    expect(screen.getByTestId('rag-probe-submit')).toBeDisabled()
    await userEvent.type(screen.getByTestId('rag-probe-input'), '   ')
    expect(screen.getByTestId('rag-probe-submit')).toBeDisabled()
    expect(searchRunbooks).not.toHaveBeenCalled()
  })

  it('surfaces a failed probe instead of silently showing nothing', async () => {
    vi.spyOn(api, 'searchRunbooks').mockRejectedValue(new api.ApiError(503, 'still initializing'))

    render(<RagInspectorView matches={[]} />)

    await userEvent.type(screen.getByTestId('rag-probe-input'), 'pool')
    await userEvent.click(screen.getByTestId('rag-probe-submit'))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('still initializing'))
  })
})
