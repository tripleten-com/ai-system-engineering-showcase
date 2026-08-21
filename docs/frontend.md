# Frontend

> **What to notice:** the War Room is an operator interface, not a decorative dashboard. It places
> impact before action, keeps dense evidence available without overwhelming the default view, and
> gives one human-in-the-loop (HITL) decision exactly one execution path.

The War Room is the React application at `services/incident-war-room/`. This chapter covers how it
is put together; [the API reference](./api-reference.md) covers the data it consumes.

## Stack

React with TypeScript, built by Vite, styled with Tailwind, tested with Vitest and Playwright.
In production the bundle is served by nginx from inside the container.

`services/incident-war-room/nginx.conf` does more than serve static files. It proxies `/api/` to
the API container, `/s3/` to LocalStack, and `/healthz` through as well. That is why the browser
talks to exactly one origin: no CORS negotiation, no absolute API URL, and no separate host for
the archived postmortems.

Two details in that config are load-bearing. `proxy_buffering off` on the `/api/` location keeps
the Server-Sent Events (SSE) stream live — a buffering proxy would hold frames until its buffer filled
and the dashboard would arrive in batches. And the `/s3/` location rewrites the path explicitly,
because nginx does not strip the matched prefix when `proxy_pass` names a variable.

The four `VITE_*` settings are build arguments rather than runtime environment; see
[operations](./operations.md#configuration).

## Component map

| File | Renders |
|---|---|
| `Header.tsx` | Brand bar, project-info disclosure, repository link |
| `BrandLogo.tsx` | The TripleTen wordmark; the only file that touches the SVG asset |
| `ScenarioControls.tsx` | The four scenario buttons and Master Reset |
| `GoldenSignalsBar.tsx` | The metric tiles and their sparklines |
| `TerminalStateBanner.tsx` | The banner shown in a terminal state |
| `AgentReasoningView.tsx` | The AI response plan console |
| `ExecutionTerminal.tsx` | The approved-action execution console |
| `LogSanitizerView.tsx` | The sensitive-log-protection console, with masked-token evidence |
| `RagInspectorView.tsx` | The recovery-guide console and the free-text probe |
| `PlanApprovalModal.tsx` | The approval dialog — plan, disclosure, approve, reject |
| `PostmortemDrawer.tsx` | The archived report, fetched and rendered |
| `MobileHitlBar.tsx` | The sticky approval entry point on narrow viewports |
| `PanelBoundary.tsx` | Error boundary around each panel |
| `Footer.tsx` | The single call to action |
| `components/ui/ConsoleFrame.tsx` | The shared console shell every output panel uses |

Supporting modules:

| Path | Owns |
|---|---|
| `src/hooks/useIncidentStream.ts` | The SSE subscription |
| `src/hooks/useTelemetryFallback.ts` | Polling when the stream is unavailable |
| `src/hooks/useConsoleAutoscroll.ts` | Tail-following and unread counting |
| `src/hooks/useMediaQuery.ts` | Breakpoint decisions in JavaScript |
| `src/hooks/useElementWidth.ts` | Width observation for the sparklines |
| `src/services/sseClient.ts` | Stream transport |
| `src/services/api.ts` | The REST calls |
| `src/lib/narration.ts` | Turns agent frames into plain-language sentences |
| `src/lib/redaction.ts` | Presentation of masked tokens |
| `src/lib/localstack.ts` | Resolves the postmortem URL |
| `src/lib/cn.ts` | Tailwind class merging |
| `src/theme/tokens.ts` | Every color, scale, and duration in the UI |

## Page order

From the hero down: scenario launcher, current-state badge, impact or outcome strip, terminal
banner, the metric tiles, the plan and worker console pair, then logs and retrieval side by side.

That order is an argument, not a layout preference. State and impact come first because they answer
"what is happening". The decision comes after the gauges it asks the reader to act on, so nobody is
asked to authorize a fix before seeing the numbers that justify it. The output streams come last
because they are evidence, consulted rather than watched.

## Where the data comes from

`useIncidentStream.ts` opens the multiplexed SSE channel and demultiplexes on the frame's `type`.
`useTelemetryFallback.ts` polls `GET /api/telemetry/current` when the stream is not available, which
keeps the metric tiles live even without the stream.

Because there is no event replay, a mid-run reload shows current state with no earlier reasoning.
The plan and worker consoles are therefore gated on whether a run exists rather than on whether any
reasoning frames have arrived — a reloaded page mid-run still needs the pair on screen.

## The approval flow

There is exactly one approval dialog. `PlanApprovalModal.tsx` is rendered once by `App.tsx`, and
both entry points — the inline trigger and the sticky `MobileHitlBar.tsx` on narrow viewports —
open that same dialog. `useMediaQuery.ts` picks which trigger is placed. One dialog means one
authorize path, which is the only way to be sure a second one has not quietly appeared.

The trigger says `Show AI action plan`; it opens the dialog and decides nothing. Inside, the button
is `Approve`, and the heading is the scenario's own approval prompt — the exact string from
`APPROVAL_PROMPT` in `packages/contracts/src/tripleten_contracts/identifiers.py`, naming the action
being authorized.

Escape, the backdrop, and the close control all dismiss without approving. Focus returns to the
trigger on dismissal. The dialog closes itself when the run leaves `AWAITING_APPROVAL`, so a
decision made elsewhere does not leave a stale dialog open.

The raw plan lives in a disclosure inside the dialog, where someone deciding whether to authorize
can read it before choosing.

## Console contract

All four output consoles use `ConsoleFrame.tsx` and share one height per breakpoint, from
`src/theme/tokens.ts`:

| Breakpoint | Height |
|---|---|
| below 768px | 360px |
| 768px–1279px | 420px |
| 1280px and up | 480px |

With a fixed 96px footer. Uniform heights are what let a 2×2 grid of consoles line up. The worker
console used to grow to fit its content; uniformity won, and the shared desktop height was raised
to 480px rather than shrinking the worker console's log area.

Behavior inside a frame:

- **Terminal reading order.** Oldest line at the top, newest at the bottom, view following the
  tail. Source arrays are chronological and the display does not reverse them.
- **Following is intent, not geometry.** It is tracked from scroll events and re-asserted each
  render, and it pauses on hover, on focus inside the frame, or when the reader scrolls more than
  24px above the bottom.
- **Unread counting.** An `N new` indicator appears while following is paused. A new run or a reset
  clears it.
- **Internal scrolling.** The frame scrolls; the page does not grow.
- **An open disclosure grows the console downward**, swapping the fixed height for the same value
  as a minimum.

## Progressive detail

Default content is concise and readable at a glance. The dense material sits one disclosure away,
behind `Technical details`:

| Console | Default | In the disclosure |
|---|---|---|
| Sensitive log protection | Plain lines plus a masked-token count | The sanitized lines themselves |
| Recovery guide search | The matched runbook and title | Retrieval evidence, cosine similarity, RRF rank, and the probe |
| AI response plan | Plain-language narration | Backend reasoning with raw tool names and arguments |
| Approved action execution | What each simulated handler reported | (nothing — see below) |

Two absences are deliberate. Neither decision-pair console carries its own `Technical details`: the
raw plan belongs in the approval dialog, where it informs a decision, and worker job metadata is not
rendered at all — it exists in the archived postmortem, which is a click away.

Plan narration is ordinal-aware and does not repeat a sentence within a run. A blocked call is
described as the first or second unsafe action, without reprinting its arguments.

## The postmortem drawer

`PostmortemDrawer.tsx` fetches the real archived JSON from the same-origin `/s3/` URL and renders
it — including the `s3://` URI and a download control.

It opens itself on success only: `HEALTHY` after a recovery, and `SECURITY_CONTAINED`. `REJECTED`
and `FAILED` never open it, because there is no postmortem to show. It can be reopened from the
worker console footer.

480px from 768px up; below that it becomes a full-screen sheet, because a 480px drawer on a 375px
phone is a scrollbar with a view attached.

## Status colors and theme

Every color, radius, duration, and font size lives in `src/theme/tokens.ts` and reaches components
only through generated Tailwind class names. Feature components hold no raw design values — no hex,
no `rgba()`, no arbitrary durations.

The foundation is a white editorial shell with warm secondary surfaces and raised dark consoles:
page `#FFFFFF`, secondary `#F2F1EE`, ink `#1A1A1A`, raised `#2A2A2A`, accent `#FF976B`.

Five status colors, each meaning exactly one thing:

| Tone | Value |
|---|---|
| healthy | `#3AA65E` |
| alarm | `#ED6F68` |
| pending | `#FFA800` |
| active | `#3F96F3` |
| guard | `#8754FD` |

Color here is a status system, never decoration — a status color used for a non-status purpose is
a defect. On light surfaces, readable text is ink and the state is carried by borders, dots, icons,
tints, or sparklines rather than by tinting the text itself.

This palette governs the React application only. Grafana keeps its own, tested separately.

Console output is white in a monospace stack rather than the phosphor green it began as: green
already means healthy in this UI, and log text is not a status.

## Accessibility

- Output streams are **not** live regions. A console emitting frames every second would make a
  screen reader unusable.
- State badges, the current-state explanation, and terminal banners announce politely. The
  explanation is a sibling of the badge's region, never an ancestor.
- Scroll regions are keyboard focusable, so a keyboard user can reach the content inside a console.
- Controls are at least 44px.
- Focus is visible, and reduced motion is honored.
- No information is carried by color alone.

## Test ids

Components expose stable `data-testid` values, and the Playwright specs in `tests/e2e/` select on
them rather than on text or structure. The ones worth knowing: `scenario-launcher`, `status-badge`,
`state-explanation`, `golden-signals`, `show-plan`, `plan-modal`, `plan-modal-prompt`,
`authorize-remediation`, `reject-remediation`, `agent-reasoning`, `execution-terminal`,
`log-sanitizer`, `rag-inspector`, `postmortem-modal`, `postmortem-open`, `terminal-state-banner`,
`master-reset`.

Changing one is a change to the test contract, not an implementation detail.
