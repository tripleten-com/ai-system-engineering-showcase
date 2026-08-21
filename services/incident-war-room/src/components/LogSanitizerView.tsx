/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        LogSanitizerView.tsx
 * Purpose:          Shows sanitized incident logs in the reusable workflow console.
 * Interacts With:   hooks/useIncidentStream.ts, components/ui/ConsoleFrame.tsx
 *
 * Curriculum Project: Project 4 — Security, Guardrails & Data Privacy
 * Skills:           Progressive Disclosure, Redaction Evidence
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * The default body summarises *what kind* of secret was masked and how many; the complete sanitized
 * lines live in `Technical details`.
 */

import { Lock, ShieldAlert } from 'lucide-react'

import type { LogEntry, ThoughtEntry } from '../hooks/useIncidentStream'
import { cn } from '../lib/cn'
import { REDACTION_TOKEN_PATTERN, REDACTION_TYPES, type RedactionKind } from '../lib/redaction'
import { GuardrailVerdict, type ScenarioId } from '../types/contracts.gen'
import { Pill } from './ui'
import { ConsoleFrame, TechnicalDetails } from './ui/ConsoleFrame'

interface LogSanitizerViewProps {
  logs: LogEntry[]
  thoughts: ThoughtEntry[]
  scenarioId: ScenarioId | null
  incidentId?: string | null
  resetKey?: string | number
}

type RedactionSummary = { kind: RedactionKind; count: number }
type LogConsoleEntry =
  | { kind: 'redaction'; id: string; summary: RedactionSummary }
  | { kind: 'blocked'; id: string; entry: ThoughtEntry }

export function segmentLogLine(message: string): Array<{ text: string; redacted: boolean }> {
  const segments: Array<{ text: string; redacted: boolean }> = []
  const pattern = new RegExp(REDACTION_TOKEN_PATTERN, 'g')
  let cursor = 0
  for (const match of message.matchAll(pattern)) {
    const start = match.index ?? 0
    if (start > cursor) segments.push({ text: message.slice(cursor, start), redacted: false })
    segments.push({ text: match[0], redacted: true })
    cursor = start + match[0].length
  }
  if (cursor < message.length) segments.push({ text: message.slice(cursor), redacted: false })
  return segments
}

export function countRedactions(logs: LogEntry[]): number {
  const pattern = new RegExp(REDACTION_TOKEN_PATTERN, 'g')
  return logs.reduce((total, log) => total + [...log.message.matchAll(pattern)].length, 0)
}

/** Rolls the log lines up into one entry per redaction kind. */
function redactionSummaries(logs: LogEntry[]): RedactionSummary[] {
  return REDACTION_TYPES.flatMap((kind) => {
    const token = `[REDACTED: ${kind}]`
    const count = logs.reduce((total, log) => total + log.message.split(token).length - 1, 0)
    return count > 0 ? [{ kind, count }] : []
  })
}

function LogLine({ log }: { log: LogEntry }) {
  return (
    <span className="font-mono text-log text-ink">
      {segmentLogLine(log.message).map((segment, index) =>
        segment.redacted ? (
          <Pill key={index} className="mx-0.5 align-baseline border-pending bg-pending/20 text-ink">
            {segment.text}
          </Pill>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </span>
  )
}

function BlockedToolCall({ thought, technical = false }: { thought: ThoughtEntry; technical?: boolean }) {
  const call = thought.tool_call
  if (!call) return null
  return (
    <span data-testid={technical ? undefined : 'blocked-tool-call'} className="block space-y-1">
      <span
        className={cn(
          'flex items-center gap-1.5 text-badge uppercase',
          // The guardrail marker keeps its own colour and glyph: a blocked call is a state, and §1
          // forbids carrying a state in colour alone or in body colour alone.
          technical ? 'font-mono text-ink' : 'font-console text-console-line text-console-output',
        )}
      >
        <ShieldAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-guard" />
        Blocked by schema firewall
      </span>
      <code
        className={cn(
          'block break-all text-log line-through',
          technical ? 'font-mono text-ink' : 'font-console text-console-line text-console-output',
        )}
      >
        {call.name}({JSON.stringify(call.args)})
      </code>
    </span>
  )
}

export function LogSanitizerView({ logs, thoughts, scenarioId, incidentId, resetKey }: LogSanitizerViewProps) {
  const blocked = thoughts.filter((thought) => thought.guardrail === GuardrailVerdict.BLOCKED && thought.tool_call !== null)
  const entries: LogConsoleEntry[] = [
    ...redactionSummaries(logs).map((summary) => ({
      kind: 'redaction' as const,
      id: `redaction-${summary.kind}`,
      summary,
    })),
    ...blocked.map((entry) => ({ kind: 'blocked' as const, id: `blocked-${entry.id}`, entry })),
  ]
  const masked = countRedactions(logs)

  return (
    <section data-testid="log-sanitizer">
      <ConsoleFrame
        title="Sensitive log protection"
        description="Secrets are removed before the AI reads the incident logs."
        entries={entries}
        entryKey={(entry) => entry.id}
        incidentId={incidentId}
        resetKey={resetKey}
        scrollTestId="log-tail"
        emptyCopy="Awaiting incident. Redaction types will appear here."
        footer={
          <div className="space-y-3">
            <p data-testid="masked-count" className="flex items-center gap-1.5 font-sans text-copy-secondary text-ink">
              <Lock aria-hidden className="h-3.5 w-3.5" />
              {masked} Sensitive Tokens Masked
            </p>
            <TechnicalDetails>
              {logs.length === 0 ? (
                <p>Complete sanitized lines appear after the incident begins.</p>
              ) : (
                <div className="space-y-2">
                  {logs.map((log) => <LogLine key={log.id} log={log} />)}
                </div>
              )}
              {blocked.map((thought) => <BlockedToolCall key={thought.id} thought={thought} technical />)}
              {scenarioId !== null && <p className="mt-2">Sanitization completed before the model received this incident.</p>}
            </TechnicalDetails>
          </div>
        }
        renderEntry={(entry) =>
          entry.kind === 'blocked' ? (
            <BlockedToolCall thought={entry.entry} />
          ) : (
            <span className="font-console text-console-line text-console-output">
              Detected {entry.summary.kind.replace('_', ' ')} redaction — {entry.summary.count} token
              {entry.summary.count === 1 ? '' : 's'} masked.
            </span>
          )
        }
      />
    </section>
  )
}
