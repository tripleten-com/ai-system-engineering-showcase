/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ConsoleFrame.tsx
 * Purpose:          A terminal-style console frame: light chrome, white machine output, newest line
 *                   at the bottom, and two explicit sizing modes.
 * Interacts With:   hooks/useConsoleAutoscroll.ts
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Streaming UI, Accessible Console Design
 * Tools:            React 18, Tailwind CSS 3
 *
 * Four decisions are worth reading the code for.
 *
 * **Chrome is light, output is dark.** The header and footer sit on the warm secondary surface in
 * ink; only the body is near-black with white output. The frame therefore reads as an *instrument on
 * a page* rather than as a black rectangle: the title and the disclosure belong to the editorial
 * shell, and everything the machine emitted is visibly separated from them.
 *
 * **The newest line is at the bottom.** A console is a transcript, and a transcript read
 * bottom-to-top makes the reader reconstruct the order themselves. The body follows the tail the way
 * a terminal does; `useConsoleAutoscroll` owns when to stop following.
 *
 * **All four consoles are one size.** They sit in a 2×2 grid, and a grid whose cells differ in height
 * for no nameable reason reads as a bug. One height scale also makes the output areas match, which is
 * what an eye actually compares. The worker console used to auto-grow instead; uniformity and
 * unbounded growth cannot both hold, and uniformity is what the grid needs.
 *
 * **An open disclosure grows the frame instead of scrolling inside it.** A bounded footer with its
 * own scrollbar put a second scroll region inside a box that already had one, and the reader had to
 * find it. Expanding now releases the fixed height and the console extends downward.
 */

import { createContext, useContext, useRef, useState, type ReactNode } from 'react'

import { useConsoleAutoscroll } from '../../hooks/useConsoleAutoscroll'
import { cn } from '../../lib/cn'

type ConsoleEntryKey = string | number
const ConsoleFooterContext = createContext<((open: boolean) => void) | null>(null)

/** The shared frame height, at the three documented widths. */
const FRAME_SIZING = 'h-console-mobile md:h-console-tablet xl:h-console-desktop'

/**
 * What the frame becomes while its disclosure is open.
 *
 * The height becomes a floor, so the console keeps its place in the row and grows downward from it
 * rather than snapping to the height of its content.
 */
const EXPANDED_SIZING = 'min-h-console-mobile md:min-h-console-tablet xl:min-h-console-desktop'

/**
 * The footer, fixed while collapsed and floored while expanded.
 *
 * Fixed is what makes four footers in a grid line up — a floor lets any two of them settle at
 * different heights the moment their content differs, and an inch of misalignment between panels on
 * one row reads as a rendering slip. It has to become a floor when a disclosure opens, though:
 * the frame clips its overflow, so a fixed footer would cut the disclosure off.
 */
const FOOTER_SIZING = 'md:h-console-footer'
const FOOTER_EXPANDED_SIZING = 'md:min-h-console-footer'

interface ConsoleFrameProps<Entry> {
  title: string
  description?: string
  entries: readonly Entry[]
  entryKey: (entry: Entry) => ConsoleEntryKey
  renderEntry: (entry: Entry) => ReactNode
  incidentId?: string | null
  resetKey?: string | number | null
  footer?: ReactNode
  scrollTestId?: string
  emptyCopy?: string
  className?: string
}

/**
 * A console shell whose chrome never scrolls away.
 *
 * Entries render in the order the stream produced them — the source arrays are chronological, and
 * the display no longer transforms that.
 */
export function ConsoleFrame<Entry>({
  title,
  description,
  entries,
  entryKey,
  renderEntry,
  incidentId,
  resetKey,
  footer,
  scrollTestId,
  emptyCopy = 'Waiting for live activity.',
  className,
}: ConsoleFrameProps<Entry>) {
  const frameRef = useRef<HTMLElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [footerExpanded, setFooterExpanded] = useState(false)
  const { orderedEntries, unreadCount, onScroll, onMouseEnter, onMouseLeave, returnToNewest } = useConsoleAutoscroll({
    entries,
    entryKey,
    incidentId,
    resetKey,
    scrollRef,
    interactionRootRef: frameRef,
  })

  return (
    <section
      ref={frameRef}
      data-testid="console-frame"
      data-console-expanded={footerExpanded ? 'true' : 'false'}
      className={cn(
        // No `min-h-0` here, deliberately. It used to sit in this list and silently beat the
        // expanded floor: twMerge cannot tell that `min-h-0` and `min-h-console-workflow-mobile`
        // are the same property when one of them is a custom key, so both were emitted and the
        // stylesheet's order decided — leaving `min-height: 0` and a frame that *shrank* when its
        // disclosure opened. The body below is the flex child that actually needs it.
        'flex flex-col overflow-hidden rounded-md border border-ink bg-raised',
        footerExpanded ? EXPANDED_SIZING : FRAME_SIZING,
        className,
      )}
    >
      <header
        data-testid="console-frame-header"
        className="flex shrink-0 items-center justify-between gap-3 border-b border-ink bg-secondary px-4 py-3"
      >
        <div>
          <h2 className="font-mono text-panel-title uppercase tracking-eyebrow text-ink">{title}</h2>
          {description && <p className="mt-1 font-sans text-copy-secondary text-ink">{description}</p>}
        </div>
        {unreadCount > 0 && (
          <button
            type="button"
            onClick={returnToNewest}
            className="rounded-sm border border-ink bg-accent px-2 py-1 font-mono text-copy-secondary text-ink transition-colors duration-status focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
          >
            {unreadCount} new
          </button>
        )}
      </header>

      <div
        ref={scrollRef}
        data-testid={scrollTestId ?? 'console-scroll-region'}
        role="group"
        aria-label={title}
        tabIndex={0}
        onScroll={onScroll}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-3 font-console text-console-line text-console-output"
      >
        {orderedEntries.length === 0 ? (
          <p className="font-console text-console-line text-console-output">{emptyCopy}</p>
        ) : (
          // No list gap: consecutive lines are a continuous transcript, and the tighter leading in
          // `console-line` carries the spacing on its own.
          <ul>
            {orderedEntries.map((entry) => (
              <li key={entryKey(entry)} data-testid="console-entry" className="break-words">
                {renderEntry(entry)}
              </li>
            ))}
          </ul>
        )}
      </div>

      {footer && (
        <footer
          data-testid="console-frame-footer"
          // `justify-end` bottom-aligns the content, so disclosure summaries and controls line up
          // across a row even when one console carries an extra line above its own. No scrollbar
          // either way: an open disclosure grows the frame downward instead.
          className={cn(
            'flex shrink-0 flex-col justify-end gap-3 border-t border-ink bg-secondary px-4 py-3 text-copy-secondary text-ink',
            footerExpanded ? FOOTER_EXPANDED_SIZING : FOOTER_SIZING,
          )}
        >
          <ConsoleFooterContext.Provider value={setFooterExpanded}>{footer}</ConsoleFooterContext.Provider>
        </footer>
      )}
    </section>
  )
}

/** A native disclosure for implementation context that starts closed and stays out of the live log. */
export function TechnicalDetails({ children }: { children: ReactNode }) {
  const setFooterExpanded = useContext(ConsoleFooterContext)
  return (
    <details
      data-testid="technical-details"
      onToggle={(event) => setFooterExpanded?.(event.currentTarget.open)}
      className="rounded-sm border border-secondary bg-secondary p-3 text-ink"
    >
      <summary className="cursor-pointer font-sans text-copy-secondary font-semibold text-ink">Technical details</summary>
      <div className="mt-3 font-sans text-body text-ink">{children}</div>
    </details>
  )
}
