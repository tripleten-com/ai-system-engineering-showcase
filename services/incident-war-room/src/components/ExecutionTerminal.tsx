/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ExecutionTerminal.tsx
 * Purpose:          Shows the worker output for approved actions, and the way back into the
 *                   archived postmortem once the drawer has been dismissed.
 * Interacts With:   hooks/useIncidentStream.ts, lib/localstack.ts, components/PostmortemDrawer.tsx
 *
 * Curriculum Project: Project 3 — Asynchronous Queues & Cloud Operations
 * Skills:           Streaming UI, Progressive Disclosure
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * Sized and scrolled exactly like the other three. It used to auto-grow instead, so its output was
 * never clipped — but it now sits in a 2×2 grid where a cell of a different height reads as a bug,
 * and uniformity and unbounded growth cannot both hold. The shared scale was raised to 480px at
 * desktop so this console's log area kept the height it had.
 *
 * The archive line is no longer linkified in place. A raw `s3://…` URI inside a log line is
 * developer-facing, and the report it points at is rendered in full by `PostmortemDrawer` — so the
 * line states that the archive happened and the footer control opens it.
 *
 * There is no `Technical details` disclosure here either, by request. The job id, source and level
 * metadata it used to hold now live only in the archived postmortem, which the drawer renders.
 */

import { FileText } from 'lucide-react'

import type { WorkerEntry } from '../hooks/useIncidentStream'
import { postmortemUrl } from '../lib/localstack'
import { ConsoleFrame } from './ui/ConsoleFrame'

/**
 * Level colouring for worker output.
 *
 * `INFO` inherits the console's neutral white; `WARN` and `ERROR` keep their status colours, because
 * a failed delivery reading the same as a successful one is the one thing this panel must not do.
 */
const LEVEL_CLASS: Record<WorkerEntry['level'], string> = {
  INFO: 'text-console-output',
  WARN: 'text-pending',
  ERROR: 'text-alarm',
}

/** What the archive line says instead of its URI. */
const ARCHIVE_CONFIRMATION = 'Postmortem archived to the LocalStack S3 incident bucket.'

function WorkerResult({ entry }: { entry: WorkerEntry }) {
  const archived = postmortemUrl(entry.message) !== null
  return (
    <span data-testid="worker-log-line" data-level={entry.level} className="break-words">
      <span className={`font-console text-console-line ${LEVEL_CLASS[entry.level]}`}>
        {archived ? ARCHIVE_CONFIRMATION : entry.message}
      </span>
    </span>
  )
}

interface ExecutionTerminalProps {
  workerLogs: WorkerEntry[]
  incidentId?: string | null
  resetKey?: string | number
  /**
   * The archived object key, when a dismissed postmortem is available to reopen.
   *
   * Null while no report exists *and* while the drawer is already open — the control is the way
   * back in, not a duplicate of what is on screen.
   */
  reopenPostmortemKey?: string | null
  onOpenPostmortem?: () => void
}

export function ExecutionTerminal({
  workerLogs,
  incidentId,
  resetKey,
  reopenPostmortemKey = null,
  onOpenPostmortem,
}: ExecutionTerminalProps) {
  return (
    <section data-testid="execution-terminal">
      <ConsoleFrame
        title="Approved action execution"
        description="Worker runs only approved action and records what happened."
        entries={workerLogs}
        entryKey={(entry) => entry.id}
        incidentId={incidentId}
        resetKey={resetKey}
        scrollTestId="worker-log"
        emptyCopy="Awaiting approval. Worker results and the postmortem link appear here."
        renderEntry={(entry) => <WorkerResult entry={entry} />}
        // The `Technical details` disclosure that used to hold the job id, source and level metadata is
        // gone by request — that detail is now only in the archived postmortem, which the drawer
        // renders in full.
        footer={
          reopenPostmortemKey !== null ? (
            <button
              type="button"
              onClick={onOpenPostmortem}
              data-testid="postmortem-open"
              className="inline-flex min-h-[44px] items-center gap-2 rounded-sm border-2 border-strong bg-accent px-4 font-mono text-badge uppercase text-ink transition-colors duration-status focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
            >
              <FileText aria-hidden className="h-4 w-4 shrink-0" />
              View postmortem
            </button>
          ) : (
            <p className="font-sans text-copy-secondary text-ink">
              The archived postmortem opens here once the approved action completes.
            </p>
          )
        }
      />
    </section>
  )
}
