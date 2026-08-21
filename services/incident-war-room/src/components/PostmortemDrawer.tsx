/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        PostmortemDrawer.tsx
 * Purpose:          Reads the JSON the worker archived to S3 and renders it as the run's report,
 *                   in a right-side drawer that opens itself when a run succeeds.
 * Interacts With:   localstack (:4566) via the war room's own `/s3/` proxy, App.tsx
 *
 * Curriculum Project: Project 3 — Asynchronous Queues & Cloud Operations
 * Skills:           Drawer Accessibility, Focus Management, Defensive Fetch Handling
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * This replaces a centred modal that only *linked* to the archive. A link asks the visitor to leave
 * the page to check the claim, and most do not — so the strongest evidence the demo has for Project 3
 * went unread. Fetching and rendering the object turns "there is a real bucket" from an assertion
 * into something the visitor is looking at.
 *
 * It is still the archive's own bytes: the response is fetched from the same-origin `/s3/` path, and
 * every field rendered comes from that JSON. `PostmortemReport` is an internal frontend view of the
 * worker's schema — the generated contracts are untouched, and the shape is validated at runtime
 * rather than asserted, because an object this page did not write could be anything.
 *
 * Shown only for the two successful endings. `REJECTED` never dispatched a job and `FAILED` never
 * completed one, so there is no report to open — and a drawer that appeared on those would present
 * a recovery that did not happen.
 *
 * Non-blocking by construction: Escape closes it, the backdrop closes it, the close control takes
 * focus on open, and the run is already over by the time it appears. A drawer that traps a visitor
 * is worse than no drawer.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Download, ExternalLink, FileCheck, RotateCcw, X } from 'lucide-react'

import { postmortemHref } from '../lib/localstack'
import { BucketName } from '../types/contracts.gen'

/**
 * The archive schema, as this page reads it.
 *
 * Mirrors `remediation_worker/postmortem.assemble`. Deliberately a frontend-local interface rather
 * than a generated type: the postmortem is an *artefact* the worker writes, not a message on the
 * API's wire, so it has no place in the shared contract package — and typing it here keeps the
 * generated file exactly as the generator produces it.
 */
export interface PostmortemReport {
  schema_version: string
  incident_id: string
  thread_id: string
  scenario_id: string
  runbook_id: string
  job_id: string
  idempotency_key: string
  completed_at: string
  authorized_by_human: boolean
  tools_executed: string[]
  /**
   * `detail` is a structured payload, not a sentence.
   *
   * `HandlerResult.detail` is a `dict[str, Any]` on the worker side — `{"pool_size": 4}`,
   * `{"message_id": "…", "destination_queue": "customer-dlq"}` — and it differs per tool. So it is
   * rendered as key/value pairs rather than as text: assuming a string here rendered an object as a
   * React child and took the whole page down.
   */
  operations: Array<{ tool: string; operation: string; detail: Record<string, unknown> }>
  execution_log: Array<{ source: string; level: string; message: string }>
}

/**
 * Validates the fetched object before anything renders it.
 *
 * Structural rather than exhaustive: the fields the drawer actually reads must be the right kind of
 * thing, because `report.operations.map` on a string is a blank page and a console stack trace. A
 * shape that fails here gets the invalid-report state, which is honest about *why* nothing rendered.
 */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isPostmortemReport(value: unknown): value is PostmortemReport {
  if (!isPlainObject(value)) return false
  const report = value
  return (
    typeof report.incident_id === 'string' &&
    typeof report.scenario_id === 'string' &&
    typeof report.runbook_id === 'string' &&
    typeof report.completed_at === 'string' &&
    typeof report.authorized_by_human === 'boolean' &&
    Array.isArray(report.tools_executed) &&
    report.tools_executed.every((tool) => typeof tool === 'string') &&
    // Element shapes are checked, not just the array-ness. `operations[].detail` is a dict on the
    // worker side, and reading it as a string handed React an object as a child and blanked the
    // page — a whole-app crash caused by a type assumption no assertion was holding.
    Array.isArray(report.operations) &&
    report.operations.every(
      (operation) =>
        isPlainObject(operation) &&
        typeof operation.tool === 'string' &&
        typeof operation.operation === 'string' &&
        isPlainObject(operation.detail),
    ) &&
    Array.isArray(report.execution_log) &&
    report.execution_log.every(
      (entry) =>
        isPlainObject(entry) &&
        typeof entry.source === 'string' &&
        typeof entry.level === 'string' &&
        typeof entry.message === 'string',
    )
  )
}

/**
 * Renders an operation's `detail` dict as readable key/value text.
 *
 * The keys differ per tool and there is no fixed set, so this formats whatever is there rather than
 * naming fields. `JSON.stringify` on the values keeps a nested object or a number from being handed
 * to React as a child — which is the defect this function exists to prevent.
 */
export function formatDetail(detail: Record<string, unknown>): string {
  return Object.entries(detail)
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' · ')
}

type Fetched =
  | { status: 'loading' }
  | { status: 'ready'; report: PostmortemReport }
  | { status: 'invalid' }
  | { status: 'error'; message: string }

interface PostmortemDrawerProps {
  /** The S3 object key, e.g. `2026-08-20-db-pool-exhaustion.json`. */
  objectKey: string
  onClose: () => void
}

/** A labelled block. One per section, so the report reads as a document rather than a JSON dump. */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section data-testid="postmortem-section" data-section={title} className="space-y-1">
      <h3 className="font-sans text-panel-title uppercase tracking-eyebrow text-ink">{title}</h3>
      <div className="font-sans text-body text-ink">{children}</div>
    </section>
  )
}

export function PostmortemDrawer({ objectKey, onClose }: PostmortemDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const [fetched, setFetched] = useState<Fetched>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const href = postmortemHref(objectKey)

  useEffect(() => {
    // Focus lands on the close control rather than on a link: the default action for a drawer the
    // visitor did not ask for should be dismissing it.
    closeRef.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => {
    // Guards against a resolved fetch from a previous key or a previous retry landing in state
    // after this effect has been torn down.
    let live = true
    setFetched({ status: 'loading' })

    void (async () => {
      try {
        const response = await fetch(href)
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`.trim())
        const body: unknown = await response.json()
        if (!live) return
        setFetched(isPostmortemReport(body) ? { status: 'ready', report: body } : { status: 'invalid' })
      } catch (error) {
        if (!live) return
        setFetched({ status: 'error', message: error instanceof Error ? error.message : 'Request failed' })
      }
    })()

    return () => {
      live = false
    }
  }, [href, attempt])

  const retry = useCallback(() => setAttempt((count) => count + 1), [])

  return (
    <div
      data-testid="postmortem-backdrop"
      onClick={onClose}
      className="fixed inset-0 z-50 bg-ink/40"
    >
      {/* Full-screen below 768px, a 480px right-hand drawer above it. Same component, because the
          two are one decision about width rather than two layouts. */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="postmortem-title"
        data-testid="postmortem-modal"
        // Stops a click inside the drawer from reaching the backdrop's dismiss handler.
        onClick={(event) => event.stopPropagation()}
        className="absolute inset-y-0 right-0 flex w-full animate-panel-enter flex-col border-l border-ink bg-page md:w-postmortem-drawer"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-ink bg-secondary px-5 py-4">
          <h2 id="postmortem-title" className="flex items-center gap-2 font-sans text-panel-title uppercase text-ink">
            <FileCheck aria-hidden className="h-4 w-4 shrink-0 text-healthy" />
            Incident postmortem
          </h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Dismiss postmortem"
            data-testid="postmortem-close"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-full border border-ink text-ink transition-colors duration-status hover:bg-page focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <p className="font-sans text-copy-secondary text-ink">
            The worker archived this report to LocalStack S3. Every field below was read back from
            that object.
          </p>

          {fetched.status === 'loading' && (
            <p data-testid="postmortem-loading" className="font-sans text-body text-ink">
              Loading the archived report…
            </p>
          )}

          {fetched.status === 'invalid' && (
            <p
              role="alert"
              data-testid="postmortem-invalid"
              className="flex items-start gap-2 rounded-sm border border-pending bg-pending/10 p-3 font-sans text-body text-ink"
            >
              <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-pending" />
              The archived object was reachable but is not a postmortem report. Download the raw JSON
              to inspect it.
            </p>
          )}

          {fetched.status === 'error' && (
            <div
              role="alert"
              data-testid="postmortem-error"
              className="space-y-3 rounded-sm border border-alarm bg-alarm/10 p-3"
            >
              <p className="flex items-start gap-2 font-sans text-body text-ink">
                <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-alarm" />
                The archived report could not be read — {fetched.message}
              </p>
              <button
                type="button"
                onClick={retry}
                data-testid="postmortem-retry"
                className="inline-flex min-h-[44px] items-center gap-2 rounded-sm border border-ink bg-secondary px-4 font-mono text-badge uppercase text-ink transition-colors duration-status hover:bg-page focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
              >
                <RotateCcw aria-hidden className="h-3.5 w-3.5 shrink-0" />
                Try again
              </button>
            </div>
          )}

          {fetched.status === 'ready' && (
            <div data-testid="postmortem-report" className="space-y-5">
              <Section title="Completion time">
                <time dateTime={fetched.report.completed_at}>{fetched.report.completed_at}</time>
              </Section>

              <Section title="Scenario">
                <span data-testid="postmortem-scenario">{fetched.report.scenario_id}</span>
              </Section>

              <Section title="Runbook">
                <span data-testid="postmortem-runbook">{fetched.report.runbook_id}</span>
              </Section>

              <Section title="Human authorization">
                {fetched.report.authorized_by_human
                  ? 'A human authorized this remediation before any tool ran.'
                  : 'This report records no human authorization.'}
              </Section>

              <Section title="Executed tools">
                <ul className="space-y-1 font-mono text-log text-ink">
                  {fetched.report.tools_executed.map((tool, index) => (
                    <li key={`${tool}-${index}`} data-testid="postmortem-tool">
                      {tool}
                    </li>
                  ))}
                </ul>
              </Section>

              <Section title="Operations">
                <ul className="space-y-2">
                  {fetched.report.operations.map((operation, index) => (
                    <li key={`${operation.tool}-${index}`} data-testid="postmortem-operation">
                      <span className="block font-semibold">{operation.operation}</span>
                      <span className="block font-mono text-log">{formatDetail(operation.detail)}</span>
                    </li>
                  ))}
                </ul>
              </Section>

              <Section title="Execution log">
                <ul className="space-y-1">
                  {fetched.report.execution_log.map((entry, index) => (
                    <li
                      key={`${entry.source}-${index}`}
                      data-testid="postmortem-log-line"
                      data-level={entry.level}
                      className="break-words font-mono text-log text-ink"
                    >
                      {entry.source} — {entry.message}
                    </li>
                  ))}
                </ul>
              </Section>
            </div>
          )}
        </div>

        <footer className="shrink-0 space-y-2 border-t border-ink bg-secondary px-5 py-4">
          <a
            href={href}
            download={objectKey}
            data-testid="postmortem-download"
            className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-sm border-2 border-strong bg-accent px-4 font-mono text-badge uppercase text-ink transition-colors duration-status focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
          >
            <Download aria-hidden className="h-4 w-4 shrink-0" />
            Download JSON
          </a>
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            data-testid="postmortem-link"
            className="inline-flex min-h-[44px] items-center gap-2 break-all font-mono text-log text-ink underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
          >
            s3://{BucketName.POSTMORTEMS}/{objectKey}
            <ExternalLink aria-hidden className="h-3.5 w-3.5 shrink-0" />
          </a>
        </footer>
      </div>
    </div>
  )
}
