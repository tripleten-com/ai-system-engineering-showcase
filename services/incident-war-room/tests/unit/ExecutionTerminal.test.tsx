/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ExecutionTerminal.test.tsx
 * Purpose:          Unit tests for the worker log tail, level colouring, the collapse/expand
 *                   control, and the working S3 postmortem link.
 * Interacts With:   ExecutionTerminal component
 *
 * Curriculum Project: Project 3 — Asynchronous Queues & Cloud Operations
 * Skills:           React Component Testing, Cloud Object Linking
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ExecutionTerminal } from '../../src/components/ExecutionTerminal'
import { postmortemUrl } from '../../src/lib/localstack'
import { BucketName, WorkerLogLevel, WorkerLogSource } from '../../src/types/contracts.gen'
import { ARCHIVE_ENTRY, POSTMORTEM_KEY, workerEntry } from '../fixtures'

describe('postmortemUrl', () => {
  it('turns the logged S3 URI into a same-origin URL', () => {
    // Relative on purpose. An absolute `http://localhost:4566` link is correct only when the viewer
    // is sitting at the machine running the stack; on a VM deployment "localhost" is their own
    // laptop and the link is dead. The war room's nginx proxies `/s3/` instead.
    const link = postmortemUrl(ARCHIVE_ENTRY.message)

    expect(link?.key).toBe(POSTMORTEM_KEY)
    expect(link?.href).toBe(`/s3/${BucketName.POSTMORTEMS}/${POSTMORTEM_KEY}`)
    expect(link?.href.startsWith('http')).toBe(false)
  })

  it('splits the surrounding prose so the anchor wraps only the URI', () => {
    const link = postmortemUrl(ARCHIVE_ENTRY.message)

    expect(link?.before).toBe('Archived report to ')
    expect(link?.after).toBe('')
  })

  it('returns null for a line with no postmortem URI', () => {
    expect(postmortemUrl('Consumed job-99214 from remediation-jobs.')).toBeNull()
  })

  it('ignores a URI in some other bucket', () => {
    // The bucket name comes from the contract. A link to an object this stack never wrote would be
    // a dead link presented as evidence.
    expect(postmortemUrl('Archived report to s3://someone-elses-bucket/report.json')).toBeNull()
  })
})

describe('ExecutionTerminal', () => {
  it('shows the standby copy before authorization', () => {
    render(<ExecutionTerminal workerLogs={[]} />)

    expect(screen.getByText(/Awaiting approval\. Worker results and the postmortem link appear here\./)).toBeInTheDocument()
  })

  it('keeps each result concise and colours a failure differently', () => {
    // There is no `Technical details` here any more, so the job id, source and level metadata it held
    // now live only in the archived postmortem — which the drawer renders in full.
    render(
      <ExecutionTerminal
        workerLogs={[
          workerEntry({ id: 'w1', source: WorkerLogSource.LOCALSTACK_SQS, message: 'Dispatched job-99214.' }),
          workerEntry({ id: 'w2', level: WorkerLogLevel.ERROR, message: 'Delivery 3 failed.' }),
        ]}
      />,
    )

    const lines = screen.getAllByTestId('worker-log-line')
    expect(lines[1].dataset.level).toBe('ERROR')
    expect(lines[1].querySelector('span:last-of-type')?.className).toContain('text-alarm')
    expect(lines[0]).not.toHaveTextContent('[LocalStack SQS]')
    expect(screen.queryByTestId('technical-details')).not.toBeInTheDocument()
  })

  it('fixes its footer height so it lines up with the other three consoles', () => {
    render(<ExecutionTerminal workerLogs={[]} />)

    const footer = screen.getByTestId('console-frame-footer')
    expect(footer).toHaveClass('md:h-console-footer')
    expect(footer).toHaveTextContent('The archived postmortem opens here')
  })

  it('confirms the archive concisely instead of printing a raw S3 URI', () => {
    // The URI is developer-facing, and the report it points at is rendered in full by the drawer.
    render(<ExecutionTerminal workerLogs={[ARCHIVE_ENTRY]} />)

    const line = screen.getByTestId('worker-log-line')
    expect(line).toHaveTextContent('Postmortem archived to the LocalStack S3 incident bucket.')
    expect(line).not.toHaveTextContent(`s3://${BucketName.POSTMORTEMS}`)
    expect(screen.queryByTestId('postmortem-link')).not.toBeInTheDocument()
    expect(screen.getByTestId('execution-terminal')).not.toHaveTextContent(POSTMORTEM_KEY)
  })

  it('offers the way back into a dismissed postmortem, and only then', async () => {
    const onOpenPostmortem = vi.fn()
    const { rerender } = render(<ExecutionTerminal workerLogs={[ARCHIVE_ENTRY]} />)
    expect(screen.queryByTestId('postmortem-open')).not.toBeInTheDocument()

    rerender(
      <ExecutionTerminal
        workerLogs={[ARCHIVE_ENTRY]}
        reopenPostmortemKey={POSTMORTEM_KEY}
        onOpenPostmortem={onOpenPostmortem}
      />,
    )

    const reopen = screen.getByTestId('postmortem-open')
    expect(reopen).toHaveTextContent('View postmortem')
    expect(reopen.className).toContain('min-h-[44px]')
    await userEvent.click(reopen)
    expect(onOpenPostmortem).toHaveBeenCalledTimes(1)
  })

  it('shares the one console height and scrolls its own body, like the other three', () => {
    const logs = Array.from({ length: 7 }, (_, index) =>
      workerEntry({ id: `w${index}`, message: `line ${index}` }),
    )

    render(<ExecutionTerminal workerLogs={logs} />)

    expect(screen.getAllByTestId('worker-log-line')).toHaveLength(7)
    // Chronological, like a terminal: the newest line is the last one, and the section grows to fit.
    expect(screen.getAllByTestId('worker-log-line').map((line) => line.textContent)).toEqual([
      'line 0',
      'line 1',
      'line 2',
      'line 3',
      'line 4',
      'line 5',
      'line 6',
    ])

    const frame = screen.getByTestId('console-frame')
    expect(frame.className).toContain('h-console-mobile')
    expect(screen.getByTestId('worker-log')).toHaveClass('overflow-y-auto')
    expect(screen.queryByTestId('terminal-expand')).not.toBeInTheDocument()
  })

})
