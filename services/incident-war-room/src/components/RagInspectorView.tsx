/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        RagInspectorView.tsx
 * Purpose:          Presents live recovery-guide retrieval and the interactive retrieval probe.
 * Interacts With:   services/api.ts, components/ui/ConsoleFrame.tsx
 *
 * Curriculum Project: Project 2 — RAG & Hybrid Retrieval
 * Skills:           Progressive Disclosure, Read-Only Probes
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * Probe results live in `Technical details` rather than in the body: they answer a question the
 * visitor asked just now, not something that happened during the run, and mixing the two would make
 * the console ambiguous about which retrieval it is showing.
 *
 * `POST /api/retrieval/search` stays read-only and never advances the state machine.
 */

import { useState, type FormEvent } from 'react'
import { Search } from 'lucide-react'

import type { RagEntry } from '../hooks/useIncidentStream'
import { cn } from '../lib/cn'
import { searchRunbooks } from '../services/api'
import { RunbookId, type RagMatchPayload } from '../types/contracts.gen'
import { ConsoleFrame, TechnicalDetails } from './ui/ConsoleFrame'

interface RagInspectorViewProps {
  matches: RagEntry[]
  incidentId?: string | null
  resetKey?: string | number
}

const DEFAULT_MATCH: RagMatchPayload = {
  runbook_id: RunbookId.RB_104,
  title: 'PostgreSQL Connection Pool Drain & Recycle',
  cosine_similarity: 0,
  rrf_rank: 0,
  excerpt: 'A recovery guide will appear here when the incident needs one.',
  source: 'Awaiting live retrieval',
}

type RagConsoleEntry =
  | { kind: 'standby'; id: string; match: RagMatchPayload }
  | { kind: 'live'; id: string; match: RagEntry }

function RecoveryGuide({ match, technical = false }: { match: RagMatchPayload; technical?: boolean }) {
  return (
    <span
      data-testid="rag-match"
      data-runbook-id={match.runbook_id}
      className={cn('block space-y-2', technical ? 'font-sans text-ink' : 'font-console text-console-line text-console-output')}
    >
      <span className={cn('block font-semibold', technical && 'font-display text-body')}>{match.title}</span>
      <span className={cn('block', technical && 'font-sans text-body')}>{match.excerpt}</span>
    </span>
  )
}

export function RagInspectorView({ matches, incidentId, resetKey }: RagInspectorViewProps) {
  const [query, setQuery] = useState('')
  const [probeResults, setProbeResults] = useState<RagMatchPayload[] | null>(null)
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)
  const entries: RagConsoleEntry[] =
    matches.length === 0
      ? [{ kind: 'standby', id: `standby-${DEFAULT_MATCH.runbook_id}`, match: DEFAULT_MATCH }]
      : matches.map((match) => ({ kind: 'live' as const, id: match.id, match }))

  const runProbe = async (event: FormEvent) => {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed || probing) return
    setProbing(true)
    setProbeError(null)
    try {
      const response = await searchRunbooks(trimmed)
      setProbeResults(response.results)
    } catch (error) {
      setProbeError(error instanceof Error ? error.message : 'Retrieval probe failed')
      setProbeResults(null)
    } finally {
      setProbing(false)
    }
  }

  const activeMatch = matches[0]
  return (
    <section data-testid="rag-inspector">
      <ConsoleFrame
        title="Recovery guide search"
        description="The system searches its internal runbooks and selects best match."
        entries={entries}
        entryKey={(entry) => entry.id}
        incidentId={incidentId}
        resetKey={resetKey}
        scrollTestId="rag-stream"
        renderEntry={(entry) => <RecoveryGuide match={entry.match} />}
        footer={
          <div>
            <TechnicalDetails>
              {activeMatch ? (
                <p data-testid="rag-source">
                  ID: {activeMatch.runbook_id} · similarity <span data-testid="cosine-similarity">{activeMatch.cosine_similarity.toFixed(4)}</span> · RRF rank {activeMatch.rrf_rank} · source {activeMatch.source}
                </p>
              ) : (
                <p>Similarity, RRF rank, source, and recovery-guide ID will appear with the live match.</p>
              )}
              <form onSubmit={runProbe} className="mt-3 space-y-2">
              <label htmlFor="rag-probe" className="block font-sans text-copy-secondary text-ink">
                Search recovery guides yourself
              </label>
              <div className="flex gap-2">
                <input
                  id="rag-probe"
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="e.g. redis keys expiring at once"
                  data-testid="rag-probe-input"
                  className="min-h-[44px] w-full rounded-sm border border-ink bg-page px-3 font-mono text-log text-ink placeholder:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
                />
                <button
                  type="submit"
                  disabled={probing || query.trim().length === 0}
                  data-testid="rag-probe-submit"
                  className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-sm border border-ink bg-accent px-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Search aria-hidden className="h-4 w-4" />
                  <span className="sr-only">Run retrieval probe</span>
                </button>
              </div>
              {probeError && <p role="alert" className="font-mono text-copy-secondary text-alarm">{probeError}</p>}
              {probeResults && (
                <div data-testid="rag-probe-results" className="space-y-2">
                  {probeResults.length === 0 ? (
                    <p className="font-sans text-copy-secondary text-ink">No runbook matched that query.</p>
                  ) : (
                    probeResults.map((match) => <RecoveryGuide key={`probe-${match.runbook_id}`} match={match} technical />)
                  )}
                </div>
              )}
              </form>
            </TechnicalDetails>
          </div>
        }
      />
    </section>
  )
}
