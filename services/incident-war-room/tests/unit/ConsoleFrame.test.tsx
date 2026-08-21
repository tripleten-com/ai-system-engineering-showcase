/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ConsoleFrame.test.tsx
 * Purpose:          Behavioral tests for the reusable terminal-style console foundation.
 * Interacts With:   ConsoleFrame, TechnicalDetails
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           React Component Testing, Accessible Console Design
 * Tools:            Vitest, React Testing Library
 *
 * The scroll assertions run against mocked box metrics because jsdom does no layout. `scrollHeight`
 * and `clientHeight` are defined per test, so "at the bottom" and "scrolled back" are expressible;
 * jsdom stores `scrollTop` verbatim, which is what makes the follow behaviour observable at all.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { ConsoleFrame, TechnicalDetails } from '../../src/components/ui/ConsoleFrame'

type Entry = { id: string; message: string }

const firstEntries: Entry[] = [
  { id: 'first', message: 'First event' },
  { id: 'second', message: 'Second event' },
]

const withThird: Entry[] = [...firstEntries, { id: 'third', message: 'Third event' }]

/** Mocked metrics: an 800px transcript in a 200px window, so the bottom is scrollTop 600. */
const SCROLL_HEIGHT = 800
const CLIENT_HEIGHT = 200
/** Anything within 24px of the bottom still counts as following. */
const AT_BOTTOM = SCROLL_HEIGHT - CLIENT_HEIGHT
const SCROLLED_BACK = 48

/**
 * Puts the reader at a scroll position, the way a browser would.
 *
 * The `fireEvent.scroll` is not decoration: the hook tracks whether the reader is following from
 * scroll *events*, because a fresh geometry measurement cannot distinguish a reader who scrolled
 * away from a box that shrank underneath one who did not. jsdom does not fire the event on a
 * `scrollTop` assignment, so the test has to.
 */
function setScrollMetrics(element: HTMLElement, scrollTop: number) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: SCROLL_HEIGHT })
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: CLIENT_HEIGHT })
  element.scrollTop = scrollTop
  fireEvent.scroll(element)
}

function renderConsole(options: Partial<React.ComponentProps<typeof ConsoleFrame<Entry>>> = {}) {
  return render(
    <ConsoleFrame
      title="Incident activity"
      entries={firstEntries}
      entryKey={(entry) => entry.id}
      renderEntry={(entry) => <span>{entry.message}</span>}
      incidentId="incident-one"
      {...options}
    />,
  )
}

/** A rerender with the same identity as `renderConsole`, differing only in what the test varies. */
function rerenderConsole(
  view: ReturnType<typeof render>,
  options: Partial<React.ComponentProps<typeof ConsoleFrame<Entry>>> = {},
) {
  view.rerender(
    <ConsoleFrame
      title="Incident activity"
      entries={withThird}
      entryKey={(entry: Entry) => entry.id}
      renderEntry={(entry: Entry) => <span>{entry.message}</span>}
      incidentId="incident-one"
      {...options}
    />,
  )
}

describe('ConsoleFrame ordering', () => {
  it('renders entries oldest-first, so the newest line is at the bottom', () => {
    // A console is a transcript. Read bottom-to-top, a causal sequence makes the reader reconstruct
    // the order themselves — step 3 above step 2 is not how any terminal presents work.
    const source = [...firstEntries]
    renderConsole({ entries: source })

    expect(screen.getAllByTestId('console-entry').map((entry) => entry.textContent)).toEqual([
      'First event',
      'Second event',
    ])
    expect(source.map((entry) => entry.id)).toEqual(['first', 'second'])
  })

  it('appends a new entry below the existing ones', () => {
    const view = renderConsole()
    rerenderConsole(view)

    expect(screen.getAllByTestId('console-entry').map((entry) => entry.textContent)).toEqual([
      'First event',
      'Second event',
      'Third event',
    ])
  })
})

describe('ConsoleFrame tail following', () => {
  it('scrolls to the newest line when the reader is already at the bottom', () => {
    const view = renderConsole()
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, AT_BOTTOM)

    rerenderConsole(view)

    expect(body.scrollTop).toBe(SCROLL_HEIGHT)
    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument()
  })

  it('keeps a reader who has scrolled back, and counts what they have not seen', () => {
    const view = renderConsole()
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, SCROLLED_BACK)

    rerenderConsole(view)

    expect(body.scrollTop).toBe(SCROLLED_BACK)
    expect(screen.getByRole('button', { name: '1 new' })).toBeInTheDocument()
  })

  it('keeps the reader position while the console is hovered or a child control is focused', async () => {
    const view = renderConsole({ renderEntry: (entry) => <button type="button">{entry.message}</button> })
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, AT_BOTTOM)

    fireEvent.mouseEnter(body)
    rerenderConsole(view, { renderEntry: (entry: Entry) => <button type="button">{entry.message}</button> })
    expect(screen.getByRole('button', { name: '1 new' })).toBeInTheDocument()

    fireEvent.mouseLeave(body)
    await userEvent.click(screen.getByRole('button', { name: 'Third event' }))
    view.rerender(
      <ConsoleFrame
        title="Incident activity"
        entries={[...withThird, { id: 'fourth', message: 'Fourth event' }]}
        entryKey={(entry: Entry) => entry.id}
        renderEntry={(entry: Entry) => <button type="button">{entry.message}</button>}
        incidentId="incident-one"
      />,
    )

    expect(screen.getByRole('button', { name: '2 new' })).toBeInTheDocument()
  })

  it('pauses auto-follow when an interactive footer child has focus', async () => {
    const view = renderConsole({ footer: <button type="button">Authorize</button> })
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, AT_BOTTOM)

    await userEvent.click(screen.getByRole('button', { name: 'Authorize' }))
    rerenderConsole(view, { footer: <button type="button">Authorize</button> })

    expect(screen.getByRole('button', { name: '1 new' })).toBeInTheDocument()
  })

  it('returns to the newest entry and clears unread entries when requested', async () => {
    const view = renderConsole()
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, SCROLLED_BACK)
    rerenderConsole(view)

    await userEvent.click(screen.getByRole('button', { name: '1 new' }))

    expect(body.scrollTop).toBe(SCROLL_HEIGHT)
    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument()
  })

  it('clears unread entries for a different non-null incident or reset key', () => {
    const view = renderConsole()
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, SCROLLED_BACK)

    rerenderConsole(view)
    expect(screen.getByRole('button', { name: '1 new' })).toBeInTheDocument()

    rerenderConsole(view, { incidentId: 'incident-two' })
    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument()

    setScrollMetrics(body, SCROLLED_BACK)
    view.rerender(
      <ConsoleFrame
        title="Incident activity"
        entries={[...withThird, { id: 'fourth', message: 'Fourth event' }]}
        entryKey={(entry: Entry) => entry.id}
        renderEntry={(entry: Entry) => <span>{entry.message}</span>}
        incidentId="incident-two"
      />,
    )
    expect(screen.getByRole('button', { name: '1 new' })).toBeInTheDocument()

    view.rerender(
      <ConsoleFrame
        title="Incident activity"
        entries={[...withThird, { id: 'fourth', message: 'Fourth event' }]}
        entryKey={(entry: Entry) => entry.id}
        renderEntry={(entry: Entry) => <span>{entry.message}</span>}
        incidentId="incident-two"
        resetKey="master-reset-1"
      />,
    )
    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument()
  })

  it('keeps following when the footer grows under a reader who has not moved', () => {
    // The regression this guards is specific and badly timed: reaching AWAITING_APPROVAL adds the
    // HITL block to the footer in the same commit as the final reasoning step. The body shrinks, so
    // a geometry check reads a following reader as a departed one — and the approval step, on the
    // one console a visitor is about to act on, lands just out of view.
    const view = renderConsole({ footer: <p>compact</p> })
    const body = screen.getByTestId('console-scroll-region')
    setScrollMetrics(body, AT_BOTTOM)

    // The footer grew: same scrollTop, more content below the fold, and no scroll event.
    Object.defineProperty(body, 'clientHeight', { configurable: true, value: CLIENT_HEIGHT - 60 })
    rerenderConsole(view, { footer: <p>a much taller footer block</p> })

    expect(body.scrollTop).toBe(SCROLL_HEIGHT)
    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument()
  })

  it('resumes following once the reader scrolls back to the bottom', () => {
    const view = renderConsole()
    const body = screen.getByTestId('console-scroll-region')

    setScrollMetrics(body, SCROLLED_BACK)
    rerenderConsole(view)
    expect(screen.getByRole('button', { name: '1 new' })).toBeInTheDocument()

    // Back at the tail by hand, the way a terminal resumes following.
    setScrollMetrics(body, AT_BOTTOM)
    view.rerender(
      <ConsoleFrame
        title="Incident activity"
        entries={[...withThird, { id: 'fourth', message: 'Fourth event' }]}
        entryKey={(entry: Entry) => entry.id}
        renderEntry={(entry: Entry) => <span>{entry.message}</span>}
        incidentId="incident-one"
      />,
    )

    expect(body.scrollTop).toBe(SCROLL_HEIGHT)
  })

  it('treats a null-to-incident transition as a reset boundary', () => {
    const view = renderConsole({ incidentId: null })
    setScrollMetrics(screen.getByTestId('console-scroll-region'), SCROLLED_BACK)

    rerenderConsole(view)

    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument()
  })
})

describe('ConsoleFrame sizing', () => {
  it('is a bounded window at the one documented height scale, shared by all four consoles', () => {
    renderConsole({ footer: <TechnicalDetails><p>Long technical payload</p></TechnicalDetails> })

    expect(screen.getByTestId('console-frame')).toHaveClass(
      'h-console-mobile',
      'md:h-console-tablet',
      'xl:h-console-desktop',
    )
    expect(screen.getByTestId('console-frame-header')).not.toHaveClass('overflow-y-auto')
    expect(screen.getByTestId('console-frame-footer')).not.toHaveClass('overflow-y-auto')
    expect(screen.getByTestId('console-scroll-region')).toHaveClass('overflow-y-auto')
    expect(screen.getByTestId('console-scroll-region')).toHaveAttribute('tabindex', '0')
    expect(screen.getByTestId('console-scroll-region')).not.toHaveAttribute('aria-live')
  })

  it('grows downward when technical details open, rather than scrolling inside its own footer', async () => {
    // A bounded footer with its own scrollbar put a second scroll region inside a box that already
    // had one, and a reader had to find it. The former height becomes a floor instead.
    renderConsole({ footer: <TechnicalDetails><p>Long technical payload</p></TechnicalDetails> })

    const frame = screen.getByTestId('console-frame')
    expect(frame).toHaveAttribute('data-console-expanded', 'false')

    await userEvent.click(screen.getByTestId('technical-details').querySelector('summary')!)

    expect(frame).toHaveAttribute('data-console-expanded', 'true')
    expect(frame).toHaveClass(
      'min-h-console-mobile',
      'md:min-h-console-tablet',
      'xl:min-h-console-desktop',
    )
    expect(frame.className).not.toMatch(/(?:^|\s)h-console-mobile/)
    // The footer becomes a floor too — the frame clips its overflow, so a fixed footer would cut an
    // open disclosure off.
    expect(screen.getByTestId('console-frame-footer')).toHaveClass('md:min-h-console-footer')
    expect(screen.getByTestId('console-frame-footer')).not.toHaveClass('overflow-y-auto')
  })
})

describe('ConsoleFrame chrome and machine output', () => {
  it('separates light chrome from the near-black output body', () => {
    renderConsole({ footer: <TechnicalDetails><p>Detail</p></TechnicalDetails> })

    for (const id of ['console-frame-header', 'console-frame-footer']) {
      expect(screen.getByTestId(id), id).toHaveClass('bg-secondary')
      expect(screen.getByTestId(id).className, id).toContain('border-ink')
    }
    expect(screen.getByRole('heading', { name: 'Incident activity' }).className).toContain('text-ink')
    expect(screen.getByTestId('console-frame-footer').className).toContain('text-ink')
    expect(screen.getByTestId('console-frame')).toHaveClass('bg-raised')
  })

  it('fixes the footer height and bottom-aligns it, so four footers in a grid line up', () => {
    // One console carries a masked-token count above its disclosure and another carries a button, so
    // a floor let them settle at different heights — which between panels on one row reads as a
    // rendering slip rather than as a difference in content.
    renderConsole({ footer: <TechnicalDetails><p>Detail</p></TechnicalDetails> })

    const footer = screen.getByTestId('console-frame-footer')
    expect(footer).toHaveClass('md:h-console-footer')
    expect(footer).toHaveClass('justify-end')
  })

  it('renders streamed output and standby copy in the console face at the tighter line height', () => {
    const body = renderConsole().container.querySelector('[data-testid="console-scroll-region"]')!
    expect(body.className).toContain('font-console')
    expect(body.className).toContain('text-console-line')
    expect(body.className).toContain('text-console-output')

    render(
      <ConsoleFrame
        title="Empty"
        entries={[]}
        entryKey={(entry: Entry) => entry.id}
        renderEntry={(entry: Entry) => <span>{entry.message}</span>}
        emptyCopy="Awaiting live activity."
      />,
    )
    const standby = screen.getByText('Awaiting live activity.')
    expect(standby.className).toContain('font-console')
    expect(standby.className).toContain('text-console-line')
    expect(standby.className).toContain('text-console-output')
  })

  it('adds no vertical gap between transcript lines', () => {
    // The tighter `console-line` leading carries the spacing; a list gap on top of it made
    // consecutive lines read as separate paragraphs.
    renderConsole()

    const list = screen.getByTestId('console-scroll-region').querySelector('ul')!
    expect(list.className).not.toMatch(/space-y-/)
  })

  it('renders no event timestamps', () => {
    // The `[mm:ss]` prefix was removed: on a demo whose whole run lasts under a minute it was four
    // near-identical characters in front of every line, and the run's own ordering already carries
    // the sequence. The envelope timestamp is still preserved on the entry models.
    renderConsole()

    expect(screen.queryByTestId('console-entry-time')).not.toBeInTheDocument()
    expect(screen.getByTestId('console-scroll-region').querySelector('time')).toBeNull()
  })

  it('keeps technical details dark-on-light inside the console', () => {
    renderConsole({ footer: <TechnicalDetails><p>Detail</p></TechnicalDetails> })

    const details = screen.getByTestId('technical-details')
    expect(details).toHaveClass('bg-secondary')
    expect(details).toHaveClass('text-ink')
  })
})

describe('TechnicalDetails', () => {
  it('is collapsed by default', () => {
    render(
      <TechnicalDetails>
        <p>Pipeline provenance and implementation detail.</p>
      </TechnicalDetails>,
    )

    expect(screen.getByTestId('technical-details')).not.toHaveAttribute('open')
    expect(screen.getByText('Technical details')).toBeInTheDocument()
  })
})
