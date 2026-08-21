/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        Header.tsx
 * Purpose:          Brand bar, live status badge, the disconnected strip, and the disclosure
 *                   chip that says what is simulated and what is real.
 * Interacts With:   incident-agent-api (/api/stream via useIncidentStream)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           State-Driven UI, Accessibility, Honest Disclosure
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 */

import { useState } from 'react'
import { ChevronDown, Github } from 'lucide-react'

import { cn } from '../lib/cn'
import type { ConnectionState } from '../services/sseClient'
import type { IncidentState } from '../types/contracts.gen'
import { BrandLogo } from './BrandLogo'
import { REPOSITORY_URL } from './Footer'
import { Panel, Pill } from './ui'

interface HeaderProps {
  state: IncidentState
  connection: ConnectionState
  reconnectAttempt: number
}

export function Header({ connection, reconnectAttempt }: HeaderProps) {
  const [disclosureOpen, setDisclosureOpen] = useState(false)

  return (
    <header className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* The wordmark carries the identity now. The programme and its "showcase" qualifier moved to
            the hero eyebrow, where they read as a subtitle to the page rather than as a second half
            of the brand's own name. */}
        <h1>
          <BrandLogo />
          <span className="sr-only">— Incident War Room</span>
        </h1>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setDisclosureOpen((open) => !open)}
            aria-expanded={disclosureOpen}
            data-testid="disclosure-toggle"
            className={cn(
              'inline-flex min-h-[44px] items-center gap-1.5 rounded-sm border border-ink bg-secondary px-3',
              'font-mono text-copy-secondary text-ink transition-colors duration-status hover:bg-page',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard',
            )}
          >
            Project info
            <ChevronDown aria-hidden className={cn('h-3.5 w-3.5 transition-transform', disclosureOpen && 'rotate-180')} />
          </button>

          <a
            href={REPOSITORY_URL}
            target="_blank"
            rel="noreferrer"
            className={cn(
              'inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 rounded-sm',
              'border border-ink bg-secondary px-3 font-mono text-copy-secondary text-ink',
              'transition-colors duration-status hover:bg-page',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard',
            )}
          >
            <Github aria-hidden className="h-3.5 w-3.5" />
            GitHub
          </a>
        </div>
      </div>

      {/* The disconnected strip. A stalled stream and a healthy system both render a flat line at
          baseline, so this must never be ambiguous — hence a persistent strip rather than a toast.

          Cold start and a lost connection are different messages, and conflating them was wrong:
          the first paint reported "DISCONNECTED — RECONNECTING (attempt 0)" while the stream was
          simply still opening for the first time. §8 asks for `Connecting to telemetry stream…`
          there, and an attempt count of zero is a giveaway that nothing has failed yet. */}
      {connection === 'connecting' && (
        <div
          role="status"
          data-testid="stream-connecting"
          className="rounded-sm border border-ink bg-secondary px-3 py-2 font-mono text-badge uppercase text-ink"
        >
          Connecting to telemetry stream…
        </div>
      )}

      {connection === 'reconnecting' && (
        <div
          role="status"
          data-testid="stream-disconnected"
          className="rounded-sm border border-pending/40 bg-pending/10 px-3 py-2 font-mono text-badge uppercase text-ink"
        >
          Telemetry stream disconnected — reconnecting (attempt {reconnectAttempt})
        </div>
      )}

      {disclosureOpen && (
        <Panel title="How this project works" testId="disclosure-panel">
          <section>
            <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">Project purpose</h3>
            <p className="font-sans text-body text-text-secondary">
              This interactive showcase brings five AI Systems Engineering skills into one
              incident-response workflow: observability, RAG retrieval, cloud queues, security
              guardrails, and AI agents with human approval. Trigger a safe, repeatable incident
              and follow it from detection to recovery.
            </p>
          </section>

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <section>
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">Infrastructure</h3>
              <ul className="space-y-2 font-sans text-body text-text-secondary">
                <li>
                  <strong className="text-ink">Incident War Room — </strong>
                  <a
                    href="http://localhost:3000"
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-copy-secondary text-ink underline underline-offset-2"
                  >
                    http://localhost:3000
                  </a>
                  : React interface for launching incidents and following the live response.
                </li>
                <li>
                  <strong className="text-ink">Incident Agent API — </strong>
                  <a
                    href="http://localhost:8000"
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-copy-secondary text-ink underline underline-offset-2"
                  >
                    http://localhost:8000
                  </a>
                  : FastAPI and LangGraph control plane that analyzes evidence and prepares recovery
                  plans.
                </li>
                <li>
                  <strong className="text-ink">Remediation Worker — internal service:</strong>{' '}
                  Receives approved SQS jobs and executes remediation actions asynchronously.
                </li>
                <li>
                  <strong className="text-ink">PostgreSQL with pgvector — </strong>
                  <Pill>localhost:5432</Pill>: Stores runbooks, vector embeddings, and LangGraph
                  checkpoints.
                </li>
                <li>
                  <strong className="text-ink">Redis — </strong>
                  <Pill>localhost:6379</Pill>: Provides caching, fast state, and worker heartbeats.
                </li>
                <li>
                  <strong className="text-ink">LocalStack — </strong>
                  <a
                    href="http://localhost:4566"
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-copy-secondary text-ink underline underline-offset-2"
                  >
                    http://localhost:4566
                  </a>
                  : Emulates AWS SQS queues and S3 storage.
                </li>
                <li>
                  <strong className="text-ink">Prometheus — </strong>
                  <a
                    href="http://localhost:9090"
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-copy-secondary text-ink underline underline-offset-2"
                  >
                    http://localhost:9090
                  </a>
                  : Collects live system metrics.
                </li>
                <li>
                  <strong className="text-ink">Grafana — </strong>
                  <a
                    href="http://localhost:3001"
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-copy-secondary text-ink underline underline-offset-2"
                  >
                    http://localhost:3001
                  </a>
                  : Displays service health and incident dashboards.
                </li>
                <li>
                  <strong className="text-ink">Jaeger — </strong>
                  <a
                    href="http://localhost:16686"
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-copy-secondary text-ink underline underline-offset-2"
                  >
                    http://localhost:16686
                  </a>
                  : Visualizes distributed request traces.
                </li>
              </ul>
            </section>

            <section>
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">Step by step</h3>
              <ol className="space-y-2 font-sans text-body text-text-secondary">
                <li>
                  <strong className="text-ink">1. Detect:</strong> A simulated failure changes the
                  live metrics and produces incident evidence.
                </li>
                <li>
                  <strong className="text-ink">2. Protect:</strong> Passwords, tokens, and private IP
                  addresses are removed before the evidence reaches the AI.
                </li>
                <li>
                  <strong className="text-ink">3. Retrieve:</strong> The system searches its RAG
                  knowledge base for the most relevant recovery runbook.
                </li>
                <li>
                  <strong className="text-ink">4. Plan:</strong> The planning stage converts the
                  evidence and runbook into a structured remediation plan.
                </li>
                <li>
                  <strong className="text-ink">5. Approve:</strong> LangGraph saves the workflow state
                  and pauses for mandatory human approval.
                </li>
                <li>
                  <strong className="text-ink">6. Execute:</strong> After approval, the API sends the
                  validated plan to an SQS queue. The worker runs only the approved actions.
                </li>
                <li>
                  <strong className="text-ink">7. Report and recover:</strong> The worker creates a
                  postmortem, stores it in S3, and reports completion. The system then returns to a
                  healthy state.
                </li>
              </ol>
            </section>

            <section>
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">
                How RAG retrieval works
              </h3>
              <div className="space-y-2 font-sans text-body text-text-secondary">
                <p>
                  A <strong className="text-ink">runbook</strong> is a step-by-step recovery guide for
                  a specific operational problem. It describes the symptoms, likely cause,
                  diagnostic checks, and safe recovery actions.
                </p>
                <p>
                  Each runbook is stored in PostgreSQL as searchable text and a 384-dimensional
                  vector embedding. For every incident, the system runs two searches:
                </p>
                <ul className="space-y-1">
                  <li>
                    <strong className="text-ink">Vector search</strong> finds runbooks with similar
                    technical vocabulary.
                  </li>
                  <li>
                    <strong className="text-ink">Full-text search</strong> finds exact keywords and
                    system terms.
                  </li>
                </ul>
                <p>
                  Reciprocal Rank Fusion (RRF) combines both rankings and selects the best match. The
                  runbook grounds the response plan in an approved recovery procedure, but its
                  instructions are not executed directly. Proposed actions must still pass security
                  validation and receive human approval.
                </p>
              </div>
            </section>

            <section>
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">Where AI is used</h3>
              <div className="space-y-2 font-sans text-body text-text-secondary">
                <p>
                  The AI model belongs at one controlled point: after log sanitization and runbook
                  retrieval, but before approval and execution.
                </p>
                <p>
                  It receives the sanitized incident evidence and selected runbook, then proposes a
                  plain-language response plan and a structured sequence of tools. Every proposed
                  call passes through the Pydantic tool firewall. The AI cannot approve its own plan
                  or execute infrastructure changes.
                </p>
                <p>
                  This showcase uses a deterministic offline planner at the same integration point
                  where a production system could connect a live LLM. This keeps the demo repeatable
                  and removes the need for an API key.
                </p>
              </div>
            </section>

            <section>
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">
                How the plan is executed
              </h3>
              <div className="space-y-2 font-sans text-body text-text-secondary">
                <p>
                  LangGraph uses a checkpointed interrupt stored in PostgreSQL to pause at the human
                  approval step.
                </p>
                <p>
                  After approval, the Incident Agent API publishes a validated job to the LocalStack
                  SQS <Pill>remediation-jobs</Pill> queue. The Remediation Worker prevents duplicate
                  execution and runs the approved tools in order.
                </p>
                <p>
                  The API contains no state-changing remediation tools, so the approval gate cannot
                  be bypassed.
                </p>
              </div>
            </section>

            <section>
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">
                How the postmortem is created
              </h3>
              <div className="space-y-2 font-sans text-body text-text-secondary">
                <p>
                  After the approved actions finish, the worker creates a structured JSON report
                  containing:
                </p>
                <ul className="space-y-1">
                  <li>The incident, scenario, and matched runbook</li>
                  <li>Confirmation of human authorization</li>
                  <li>The tools and operations executed</li>
                  <li>Sanitized execution logs</li>
                  <li>The completion time</li>
                </ul>
                <p>
                  The report is uploaded to the LocalStack S3{' '}
                  <Pill>tripleten-cloud-postmortems</Pill> bucket. The worker sends its location back
                  to the API, and the War Room automatically opens the completed report.
                </p>
              </div>
            </section>

            <section className="lg:col-span-2">
              <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">
                Incident scenarios
              </h3>
              <ul className="grid gap-2 font-sans text-body text-text-secondary md:grid-cols-2">
                <li>
                  <strong className="text-ink">Database overload:</strong> Leaked connections fill the
                  database pool. The worker terminates idle connections and recycles the pool.
                </li>
                <li>
                  <strong className="text-ink">Cache traffic spike:</strong> Many requests miss the
                  cache together. The worker removes stale keys, warms frequently used data, and adds
                  TTL jitter.
                </li>
                <li>
                  <strong className="text-ink">Queue processing stops:</strong> A corrupted message
                  blocks the workers. The message moves to a dead-letter queue, the workers restart,
                  and the backlog drains.
                </li>
                <li>
                  <strong className="text-ink">Prompt injection attempt:</strong> Security guardrails
                  block unsafe tool calls automatically. After approval, the worker revokes the
                  session, blocks the source IP, and archives forensic evidence. No outage occurs.
                </li>
              </ul>
            </section>
          </div>

          <section className="mt-6">
            <h3 className="mb-2 font-sans text-panel-title uppercase text-ink">
              Simulated and Real
            </h3>
            <p className="font-sans text-copy-secondary text-text-secondary">
              The incidents, metric profiles, remediation effects, and AI planner are simulated so
              the demo remains safe and repeatable. Sanitization, hybrid retrieval, approval
              checkpoints, security validation, queue dispatch, distributed tracing, state
              persistence, and postmortem storage run as real system components.
            </p>
          </section>
        </Panel>
      )}
    </header>
  )
}
