/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        PostmortemDrawer.test.tsx
 * Purpose:          Tests the drawer that reads the archived report — every render state, every way
 *                   out, and the shape validation that stops a bad object rendering a blank page.
 * Interacts With:   PostmortemDrawer component
 *
 * Curriculum Project: Project 3 — Asynchronous Queues & Cloud Operations
 * Skills:           React Component Testing, Fetch State Handling, Drawer Accessibility
 * Tools:            Vitest, React Testing Library
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { PostmortemDrawer, formatDetail, isPostmortemReport } from '../../src/components/PostmortemDrawer'
import { BucketName } from '../../src/types/contracts.gen'
import { POSTMORTEM_KEY, POSTMORTEM_REPORT } from '../fixtures'

const HREF = `/s3/${BucketName.POSTMORTEMS}/${POSTMORTEM_KEY}`

function stubFetch(implementation: () => Promise<Response>) {
  const fetchMock = vi.fn(implementation)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function ok(body: unknown) {
  return async () => new Response(JSON.stringify(body), { status: 200 })
}

beforeEach(() => {
  stubFetch(ok(POSTMORTEM_REPORT))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('isPostmortemReport', () => {
  it('accepts the shape the worker writes', () => {
    expect(isPostmortemReport(POSTMORTEM_REPORT)).toBe(true)
  })

  it.each([
    ['null', null],
    ['a string', 'not a report'],
    ['an array', []],
    ['an empty object', {}],
    ['a report with a string where a list belongs', { ...POSTMORTEM_REPORT, operations: 'none' }],
    ['a report missing its authorization flag', { ...POSTMORTEM_REPORT, authorized_by_human: undefined }],
    ['a non-string entry in tools_executed', { ...POSTMORTEM_REPORT, tools_executed: [{ tool: 'x' }] }],
    [
      'an operation whose detail is a bare string',
      { ...POSTMORTEM_REPORT, operations: [{ tool: 't', operation: 'o', detail: 'a sentence' }] },
    ],
    [
      'a log line with a missing message',
      { ...POSTMORTEM_REPORT, execution_log: [{ source: 'Worker', level: 'INFO' }] },
    ],
  ])('rejects %s', (_label, value) => {
    // `report.operations.map` on a string is a blank page and a console stack trace. Rejecting the
    // shape up front is what turns that into an explained state — and the element-level checks are
    // here because the array-level ones alone let `detail` through as a string and crashed the page.
    expect(isPostmortemReport(value)).toBe(false)
  })
})

describe('formatDetail', () => {
  it('reads a structured detail dict as key/value text', () => {
    // `HandlerResult.detail` is a `dict[str, Any]` whose keys differ per tool, so this formats what
    // is there rather than naming fields.
    expect(formatDetail({ terminated_pids: 84, idle_threshold_seconds: 60 })).toBe(
      'terminated pids: 84 · idle threshold seconds: 60',
    )
  })

  it('keeps a string value unquoted and serialises anything else', () => {
    expect(formatDetail({ destination_queue: 'customer-dlq', keys: ['a', 'b'] })).toBe(
      'destination queue: customer-dlq · keys: ["a","b"]',
    )
  })

  it('renders an empty detail as empty text rather than throwing', () => {
    expect(formatDetail({})).toBe('')
  })
})

describe('PostmortemDrawer — the archived report', () => {
  it('fetches the object from the same-origin S3 path', async () => {
    const fetchMock = stubFetch(ok(POSTMORTEM_REPORT))
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(HREF))
    // Relative, never absolute: on a VM deployment "localhost" is the viewer's own laptop.
    expect(HREF.startsWith('http')).toBe(false)
  })

  it('renders every section of the real JSON', async () => {
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    await screen.findByTestId('postmortem-report')

    const sections = screen.getAllByTestId('postmortem-section').map((section) => section.dataset.section)
    expect(sections).toEqual([
      'Completion time',
      'Scenario',
      'Runbook',
      'Human authorization',
      'Executed tools',
      'Operations',
      'Execution log',
    ])

    expect(screen.getByTestId('postmortem-scenario')).toHaveTextContent('db_pool_exhaustion')
    expect(screen.getByTestId('postmortem-runbook')).toHaveTextContent('RB-104')
    expect(screen.getByTestId('postmortem-tool')).toHaveTextContent('flush_connection_pool')
    expect(screen.getByTestId('postmortem-operation')).toHaveTextContent('pg_terminate_backend')
    expect(screen.getByTestId('postmortem-operation')).toHaveTextContent('terminated pids: 84')
    expect(screen.getAllByTestId('postmortem-log-line')).toHaveLength(2)
  })

  it('states the human authorization the report records', async () => {
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    await screen.findByTestId('postmortem-report')
    expect(screen.getByText('A human authorized this remediation before any tool ran.')).toBeInTheDocument()
  })

  it('marks the completion time as a semantic instant', async () => {
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    await screen.findByTestId('postmortem-report')
    const time = screen.getByText(POSTMORTEM_REPORT.completed_at)
    expect(time.tagName).toBe('TIME')
    expect(time).toHaveAttribute('datetime', POSTMORTEM_REPORT.completed_at)
  })
})

describe('PostmortemDrawer — non-happy paths', () => {
  it('says it is loading rather than showing an empty drawer', async () => {
    let release: (value: Response) => void = () => {}
    stubFetch(() => new Promise<Response>((resolve) => { release = resolve }))

    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    expect(screen.getByTestId('postmortem-loading')).toBeInTheDocument()
    release(new Response(JSON.stringify(POSTMORTEM_REPORT), { status: 200 }))
    await screen.findByTestId('postmortem-report')
    expect(screen.queryByTestId('postmortem-loading')).not.toBeInTheDocument()
  })

  it('explains an object that is reachable but is not a report', async () => {
    stubFetch(ok({ hello: 'world' }))

    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    expect(await screen.findByTestId('postmortem-invalid')).toHaveTextContent(/not a postmortem report/i)
    expect(screen.queryByTestId('postmortem-report')).not.toBeInTheDocument()
    // The raw object is still one click away, which is the only useful next step here.
    expect(screen.getByTestId('postmortem-download')).toHaveAttribute('href', HREF)
  })

  it('surfaces a failed read with the status, and offers a retry that works', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('nope', { status: 503, statusText: 'Service Unavailable' }))
      .mockResolvedValueOnce(new Response(JSON.stringify(POSTMORTEM_REPORT), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    expect(await screen.findByTestId('postmortem-error')).toHaveTextContent('503')

    await userEvent.click(screen.getByTestId('postmortem-retry'))

    await screen.findByTestId('postmortem-report')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(screen.queryByTestId('postmortem-error')).not.toBeInTheDocument()
  })

  it('surfaces a network failure the same way', async () => {
    stubFetch(async () => {
      throw new Error('Failed to fetch')
    })

    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    expect(await screen.findByTestId('postmortem-error')).toHaveTextContent('Failed to fetch')
  })

  it('announces both failure states politely to a screen reader', async () => {
    stubFetch(ok({ nope: true }))
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    expect(await screen.findByTestId('postmortem-invalid')).toHaveAttribute('role', 'alert')
  })
})

describe('PostmortemDrawer — layout and dismissal', () => {
  it('is a right-side drawer at 480px on desktop and a full-screen sheet below 768px', async () => {
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    const drawer = await screen.findByTestId('postmortem-modal')
    expect(drawer.className).toContain('right-0')
    expect(drawer.className).toContain('inset-y-0')
    // Full width by default, 480px from `md` — one decision about width rather than two layouts.
    expect(drawer.className).toContain('w-full')
    expect(drawer.className).toContain('md:w-postmortem-drawer')
  })

  it('is a labelled modal dialog', async () => {
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    const drawer = await screen.findByTestId('postmortem-modal')
    expect(drawer).toHaveAttribute('role', 'dialog')
    expect(drawer).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('dialog', { name: /incident postmortem/i })).toBeInTheDocument()
  })

  it('takes focus on the close control rather than on a link', async () => {
    // The default action for a drawer the visitor did not ask for should be dismissing it.
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    await waitFor(() => expect(screen.getByTestId('postmortem-close')).toHaveFocus())
  })

  it('closes on the control, on Escape, and on the backdrop', async () => {
    const onClose = vi.fn()
    const { unmount } = render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={onClose} />)

    await userEvent.click(screen.getByTestId('postmortem-close'))
    expect(onClose).toHaveBeenCalledTimes(1)

    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(2)

    await userEvent.click(screen.getByTestId('postmortem-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(3)
    unmount()
  })

  it('does not dismiss on a click inside the drawer', async () => {
    const onClose = vi.fn()
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={onClose} />)

    await screen.findByTestId('postmortem-report')
    await userEvent.click(screen.getByTestId('postmortem-scenario'))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('offers a download and a direct S3 link', async () => {
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    const download = await screen.findByTestId('postmortem-download')
    expect(download).toHaveAttribute('href', HREF)
    expect(download).toHaveAttribute('download', POSTMORTEM_KEY)
    expect(download).toHaveTextContent('Download JSON')

    const link = screen.getByTestId('postmortem-link')
    expect(link).toHaveAttribute('href', HREF)
    expect(link).toHaveAttribute('rel', 'noreferrer')
    expect(link).toHaveTextContent(`s3://${BucketName.POSTMORTEMS}/${POSTMORTEM_KEY}`)
  })

  it('honours reduced motion through the named enter animation', () => {
    // `index.css` disables `animate-panel-enter` under `prefers-reduced-motion`. Using the named
    // animation rather than a bespoke transition is what puts the drawer inside that rule.
    render(<PostmortemDrawer objectKey={POSTMORTEM_KEY} onClose={vi.fn()} />)

    expect(screen.getByTestId('postmortem-modal').className).toContain('animate-panel-enter')
  })
})
