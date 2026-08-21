/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        App.tsx
 * Purpose:          The war room layout and the only place local action state lives — trigger,
 *                   authorize, reject, reset.
 * Interacts With:   incident-agent-api (:8000) via hooks/ and services/
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Command Center Layout, Responsive Design, State Presentation
 * Tools:            React 18, Vite, Tailwind CSS
 *
 * Layout follows `spa-design-guidelines.md` §7 exactly, and the four breakpoints are four different
 * layouts rather than one layout scaled down:
 *
 * | Width | Signals | Workflow | Terminal |
 * |---|---|---|---|
 * | ≥1280 (`xl`) | 4-across | 3-across | full width |
 * | 1024–1279 (`lg`) | 4-across | 3-across, narrower | 3-line tail |
 * | 768–1023 (`md`) | 2×2 | logs + RAG side by side, agent full width beneath | 3-line tail |
 * | <768 | 2×2 | single column in pipeline order | 3-line tail, expandable |
 *
 * Below `768px` the HITL pair leaves the agent panel entirely and pins to a sticky bottom bar. That
 * is the one responsive decision the demo's success actually depends on.
 *
 * State that belongs to the *server* comes from `useIncidentStream` and is never mirrored here.
 * What lives in this component is only what the server cannot know: whether a request this tab
 * issued is still in flight, what it failed with, and whether the visitor has dismissed the
 * postmortem. Keeping run state out of local `useState` is what makes a browser refresh mid-run
 * recover cleanly instead of showing a half-remembered incident.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { AgentReasoningView } from './components/AgentReasoningView'
import { ExecutionTerminal } from './components/ExecutionTerminal'
import { Footer } from './components/Footer'
import { GoldenSignalsBar } from './components/GoldenSignalsBar'
import { Header } from './components/Header'
import { LogSanitizerView } from './components/LogSanitizerView'
import { MobileHitlBar } from './components/MobileHitlBar'
import { PanelBoundary } from './components/PanelBoundary'
import { PlanApprovalModal } from './components/PlanApprovalModal'
import { PostmortemDrawer } from './components/PostmortemDrawer'
import { RagInspectorView } from './components/RagInspectorView'
import { ScenarioControls } from './components/ScenarioControls'
import { TerminalStateBanner, isTerminalState } from './components/TerminalStateBanner'
import { StatusBadge } from './components/ui'
import { useIncidentStream } from './hooks/useIncidentStream'
import { postmortemUrl } from './lib/localstack'
import { currentStateExplanation, SCENARIO_IMPACT, SCENARIO_OUTCOME } from './lib/narration'
import { BELOW_MD, useMediaQuery } from './hooks/useMediaQuery'
import { useTelemetryFallback } from './hooks/useTelemetryFallback'
import { ApiError, authorizeIncident, resetIncident, triggerIncident } from './services/api'
import { ScenarioId, type IncidentState } from './types/contracts.gen'

/**
 * The states in which a live incident is costing something.
 *
 * Scenario 4 keeps `SECURITY_CONTAINED` in this set and the outages do not, because its strip is a
 * standing claim rather than a running cost: `No customer impact — 0 unauthorized actions` has to
 * remain true and visible through containment, which is the assertion the whole scenario exists to
 * make. An outage's impact strip, by contrast, stops being true the moment recovery begins.
 */
const IMPACT_PHASES = new Set<IncidentState>(['CRITICAL_OUTAGE', 'EXPLOIT_INTERCEPTED', 'AWAITING_APPROVAL', 'EXECUTING'])

/** The two endings that produce an archived report and a success message. */
const SUCCESS_STATES = new Set<IncidentState>(['HEALTHY', 'SECURITY_CONTAINED'])

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.status} — ${error.message}`
  return error instanceof Error ? error.message : 'Request failed'
}

export function App() {
  const stream = useIncidentStream()
  const { state, incidentId, threadId, scenarioId, lastScenarioId, stale, rehydrate, clearRun, clearPanels } = stream

  // Polling takes over only while the stream is down; the disconnected strip stays up throughout,
  // because a degraded mode the UI hides is indistinguishable from a working one.
  useTelemetryFallback({ active: stale, poll: rehydrate })

  const [busy, setBusy] = useState(false)
  // Tagged with the control that produced it. One untagged string was rendered by both
  // `ScenarioControls` and `AgentReasoningView`, so a refused authorize also printed an error under
  // the scenario buttons — pointing the viewer at the wrong control.
  const [actionError, setActionError] = useState<{ source: 'control' | 'decision'; message: string } | null>(null)
  const [dismissedPostmortem, setDismissedPostmortem] = useState<string | null>(null)
  const [planModalOpen, setPlanModalOpen] = useState(false)
  const [consoleResetKey, setConsoleResetKey] = useState(0)

  // The S3 key comes from the worker's own log line rather than from a date computed in the
  // browser. The worker's clock wrote the object; a client-side `new Date()` would disagree with it
  // across a UTC midnight and link to a key that does not exist.
  const postmortemKey = useMemo(() => {
    for (const entry of [...stream.workerLogs].reverse()) {
      const link = postmortemUrl(entry.message)
      if (link) return link.key
    }
    return null
  }, [stream.workerLogs])

  // Both successful endings archive a report, and both open the drawer. `REJECTED` never dispatched
  // a job and `FAILED` never completed one, so neither has a report to show — which is why this is
  // keyed on the two success states rather than on "the run is over".
  const postmortemAvailable = SUCCESS_STATES.has(state) && postmortemKey !== null
  const showPostmortem = postmortemAvailable && dismissedPostmortem !== postmortemKey

  /**
   * The scenario the copy speaks about, including a finished run's.
   *
   * The server stops reporting a scenario the moment a run completes, but "Database capacity is
   * restored" is exactly the sentence a visitor needs at that moment — so the completed-state copy
   * reads the last scenario seen. Master Reset clears it, which returns the page to its
   * choose-a-scenario copy.
   */
  const narratedScenario = scenarioId ?? lastScenarioId
  const stateExplanation = currentStateExplanation(state, narratedScenario)

  const showImpactStrip =
    narratedScenario !== null &&
    (IMPACT_PHASES.has(state) ||
      // Scenario 4's strip is the exception described above: it outlives the run and stays true.
      (narratedScenario === ScenarioId.PROMPT_INJECTION && state === 'SECURITY_CONTAINED'))
  const showOutcomeStrip = narratedScenario !== null && SUCCESS_STATES.has(state)

  /**
   * Whether the plan and worker consoles are on the page at all.
   *
   * Keyed on `lastRunId` — "a run exists, or has" — for two reasons. It is not gated on
   * `thoughts.length`, because the SSE stream has no replay buffer: a browser reloaded mid-run
   * rebuilds from the snapshot with an empty reasoning chain, and the authorize control must never
   * be missing while the state machine sits at the gate waiting for it. And it is not gated on the
   * scenario, because only *snapshots* carry `scenario_id` — a run observed purely through metrics
   * frames has an incident id and no scenario, and would have rendered no plan at all.
   *
   * It stays up for a finished run, because that is where the postmortem reopen control lives.
   * Master Reset clears `lastRunId`, which returns the page to its steady state.
   */
  const showDecisionSection = stream.lastRunId !== null

  // A new run must not inherit the previous run's error. Keyed on the incident rather than cleared
  // in the trigger handler, so a run started in another tab clears it here too.
  useEffect(() => {
    setActionError(null)
  }, [incidentId])

  // The modal exists only for an open decision. Closing it on the state leaving `AWAITING_APPROVAL`
  // covers all three ways that happens — this tab approved, this tab rejected, or another tab did —
  // and means the dialog can never outlive the gate it belongs to.
  useEffect(() => {
    if (state !== 'AWAITING_APPROVAL') setPlanModalOpen(false)
  }, [state])

  const runAction = useCallback(
    async (source: 'control' | 'decision', action: () => Promise<unknown>) => {
      setBusy(true)
      setActionError(null)
      try {
        await action()
        // The stream carries the new state within a second, but rehydrating immediately makes the
        // click feel answered rather than merely accepted.
        await rehydrate()
      } catch (error) {
        setActionError({ source, message: errorMessage(error) })
      } finally {
        setBusy(false)
      }
    },
    [rehydrate],
  )

  const onTrigger = useCallback(
    (scenario: ScenarioId) =>
      void runAction('control', async () => {
        const response = await triggerIncident(scenario)
        // Clear the previous run's panels the moment the trigger is accepted, rather than waiting
        // for a frame carrying the new incident id to arrive and do it. Those are up to a second
        // apart, and in that window a finished plan sat on screen presented as the new run's.
        // Only the derived panels go — the identifiers stay, so the plan and worker consoles do not
        // blink out of existence between the click and the first frame.
        clearPanels()
        setConsoleResetKey((key) => key + 1)
        return response
      }),
    [runAction, clearPanels],
  )

  const onReset = useCallback(() => {
    if (!incidentId) return
    setDismissedPostmortem(null)
    void runAction('control', async () => {
      const response = await resetIncident(incidentId)
      // Only after the server confirms. Clearing optimistically would blank the panels on a 409 and
      // leave the viewer looking at an empty war room with the run still in flight.
      clearRun()
      setConsoleResetKey((key) => key + 1)
      return response
    })
  }, [incidentId, runAction, clearRun])

  const onDecision = useCallback(
    (approved: boolean) => {
      // All three identifiers are required by the API, and a missing one means this tab has not
      // caught up with the run yet — sending a partial body would earn a 422 rather than a decision.
      if (!incidentId || !threadId || !scenarioId) {
        setActionError({
          source: 'decision',
          message: 'The run identifiers are not loaded yet; wait for the next telemetry frame.',
        })
        return
      }
      void runAction('decision', () => authorizeIncident(incidentId, threadId, scenarioId, approved))
    },
    [incidentId, threadId, scenarioId, runAction],
  )

  const awaitingApproval = state === 'AWAITING_APPROVAL' && scenarioId !== null

  // Which of the two HITL placements exists. Exactly one, never both — see useMediaQuery.
  const hitlOnStickyBar = useMediaQuery(BELOW_MD)

  return (
    <div className="min-h-screen bg-page p-4 lg:p-6">
      {/* `pb-32` below `md` keeps the sticky HITL bar from covering the terminal's last lines. */}
      <div className={`mx-auto w-full max-w-showcase space-y-6 ${awaitingApproval ? 'pb-32 md:pb-0' : ''}`}>
        <Header state={state} connection={stream.connection} reconnectAttempt={stream.reconnectAttempt} />

        <section data-testid="showcase-hero" className="grid gap-6 border-y border-subtle py-6 lg:grid-cols-2 lg:items-start">
          <div className="space-y-5">
            <p className="font-mono text-eyebrow uppercase text-ink">
              TripleTen AI Systems Engineering - Showcase Project
            </p>
            <h2 className="max-w-3xl font-display text-hero font-semibold text-ink md:text-hero-desktop">
              See how AI systems respond to a production incident
            </h2>
            <p className="max-w-2xl font-sans text-body text-ink md:text-hero-body">
              Trigger a realistic failure. Watch the system protect sensitive data, find the right recovery guide, propose a fix, wait for human approval, and recover.
            </p>
            {/* The proof-point list used to sit here. It restated the sentence above it in three
                fragments, which is the kind of copy that reads as marketing rather than as
                description — and the demo below proves each claim by doing it. */}
          </div>
          <ScenarioControls
            state={state}
            incidentId={incidentId}
            busy={busy || stream.loading}
            error={actionError?.source === 'control' ? actionError.message : null}
            explanation={stateExplanation}
            onTrigger={onTrigger}
            onReset={onReset}
          />
        </section>

        {/* The run's state, its consequences, and the decision it is waiting on — all above the
            charts, in that order.

            The charts are the *evidence* for what these say, not the headline. A visitor who has
            just clicked a trigger wants to know what is happening and what is being asked of them;
            reading four gauges and inferring it is work this page can do for them. So the order
            from the divider down is: state, then what it costs, then the plan and the button. */}
        <div data-testid="run-status" className="flex items-center gap-3">
          <span className="font-sans text-copy-secondary text-ink">Current state</span>
          <div aria-live="polite"><StatusBadge state={state} /></div>
        </div>

        {/* What the incident is costing, while it is costing it.
            Scenario 4 keeps its established `no-impact-strip` identity and its wording: it appears
            when the guardrail fires and stays through containment, and it is true the whole time —
            the three containment tools are authorized, and they run only after the click. */}
        {showImpactStrip && narratedScenario !== null && (
          <div
            data-testid={narratedScenario === ScenarioId.PROMPT_INJECTION ? 'no-impact-strip' : 'scenario-impact-strip'}
            data-scenario={narratedScenario}
            className={`rounded-md border px-4 py-2 text-center font-mono text-badge uppercase text-ink ${
              narratedScenario === ScenarioId.PROMPT_INJECTION ? 'border-guard bg-guard/10' : 'border-alarm bg-alarm/10'
            }`}
          >
            {SCENARIO_IMPACT[narratedScenario]}
          </div>
        )}

        {/* And what the run achieved. Only on the two successful endings — `REJECTED` and `FAILED`
            keep their terminal banner, and a success message beside a failure banner would be the
            page contradicting itself. */}
        {showOutcomeStrip && narratedScenario !== null && (
          <div
            data-testid="scenario-outcome-strip"
            data-scenario={narratedScenario}
            className="rounded-md border border-healthy bg-healthy/10 px-4 py-2 text-center font-mono text-badge uppercase text-ink"
          >
            {SCENARIO_OUTCOME[narratedScenario]}
          </div>
        )}

        {isTerminalState(state) && (
          <TerminalStateBanner
            state={state}
            failureReason={stream.failureReason}
            busy={busy}
            onReset={onReset}
          />
        )}

        {/* Rendered here rather than at the end of the tree even though it is `position: fixed`.
            Tab order follows the DOM, and §11 requires the HITL controls to be reachable before the
            log stream — a sticky bar appended last would be visually first and last to tab to. */}
        {awaitingApproval && scenarioId !== null && hitlOnStickyBar && (
          <MobileHitlBar busy={busy} onShowPlan={() => setPlanModalOpen(true)} />
        )}

        <GoldenSignalsBar
          state={state}
          scenarioId={scenarioId}
          goldenSignals={stream.goldenSignals}
          infrastructure={stream.infrastructure}
          history={stream.history}
          stale={stale}
          loading={stream.loading}
        />

        {/* The decision and its outcome, as one paired section under the charts.
            The plan says what is proposed and the worker shows what the approval did, so reading one
            without the other is half the argument — they appear and disappear together, and only
            once a run exists, so the steady-state page is not two consoles of standby copy.

            Still ahead of the log and retrieval streams, which is what §11's tab-order rule is
            actually about: the charts in between are not focusable. */}
        {showDecisionSection && (
          <div data-testid="decision-grid" className="grid items-start gap-4 lg:grid-cols-2">
            <PanelBoundary title="AI response plan">
              <AgentReasoningView
                state={state}
                scenarioId={scenarioId}
                thoughts={stream.thoughts}
                busy={busy}
                error={actionError?.source === 'decision' ? actionError.message : null}
                onShowPlan={() => setPlanModalOpen(true)}
                hitlInline={!hitlOnStickyBar}
                incidentId={incidentId}
                resetKey={consoleResetKey}
              />
            </PanelBoundary>

            <PanelBoundary title="Approved action execution">
              <ExecutionTerminal
                workerLogs={stream.workerLogs}
                incidentId={incidentId}
                resetKey={consoleResetKey}
                // Offered only once the drawer has been dismissed: while it is open the control would
                // be a second door into the room the visitor is already standing in.
                reopenPostmortemKey={postmortemAvailable && !showPostmortem ? postmortemKey : null}
                onOpenPostmortem={() => setDismissedPostmortem(null)}
              />
            </PanelBoundary>
          </div>
        )}

        {/* Two columns now that the plan has moved out of this grid, and no CSS `order` needed: the
            visible pipeline reads logs then retrieval, which is also the DOM order.

            `items-start` matters more than it looks. A CSS grid stretches every item to the tallest
            row by default, which left the shorter panel as a tall band of empty surface. Ragged
            bottom edges are a much smaller cost, and this is the frame the screenshots are taken
            from. */}
        <div data-testid="workflow-grid" className="grid items-start gap-4 md:grid-cols-2">
          <PanelBoundary title="Sensitive log protection">
            <LogSanitizerView
              logs={stream.logs}
              thoughts={stream.thoughts}
              scenarioId={scenarioId}
              incidentId={incidentId}
              resetKey={consoleResetKey}
            />
          </PanelBoundary>

          <PanelBoundary title="Recovery guide search">
            <RagInspectorView matches={stream.ragMatches} incidentId={incidentId} resetKey={consoleResetKey} />
          </PanelBoundary>
        </div>

        <Footer />
      </div>

      {/* The approval decision. Rendered once here rather than in either HITL placement, so the
          inline footer and the sticky mobile bar open the same dialog and there is never a second
          authorize control in the DOM. */}
      {planModalOpen && awaitingApproval && scenarioId !== null && (
        <PlanApprovalModal
          scenarioId={scenarioId}
          thoughts={stream.thoughts}
          busy={busy}
          error={actionError?.source === 'decision' ? actionError.message : null}
          onDecision={onDecision}
          onClose={() => setPlanModalOpen(false)}
        />
      )}

      {/* Boundaried like every other output, and for a reason this change learned the hard way: the
          drawer renders an artefact written by another process, and one field that was a dict rather
          than the string this page assumed took the entire war room down mid-run. The shape is now
          validated before anything renders — and if a future field slips past that, this degrades
          the drawer instead of the page. */}
      {showPostmortem && postmortemKey !== null && (
        <PanelBoundary title="Incident postmortem">
          <PostmortemDrawer objectKey={postmortemKey} onClose={() => setDismissedPostmortem(postmortemKey)} />
        </PanelBoundary>
      )}
    </div>
  )
}

export default App
