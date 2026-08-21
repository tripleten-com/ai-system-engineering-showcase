/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        MobileHitlBar.tsx
 * Purpose:          Pins the authorize / reject pair to a sticky bottom bar below 768px.
 * Interacts With:   components/AgentReasoningView.tsx (HitlControls), App.tsx
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Responsive Design, Mobile UX, Accessibility
 * Tools:            React 18, Tailwind CSS
 *
 * `spa-design-guidelines.md` §7 calls this "the most important responsive decision in the
 * document", and the reasoning is worth restating: the entire demo depends on the visitor pressing
 * that button. On a phone the agent column sits under the signals, the logs, and the RAG panel — if
 * the gate is down there, the pipeline stalls at `AWAITING_APPROVAL` and the visitor leaves
 * believing the demo is broken.
 *
 * It renders the same `ShowPlanButton` the inline footer uses. A second, hand-written copy would
 * drift — different label, different disabled logic — and the inline block is suppressed below `md`
 * precisely so there is never a second one of these in the tab order. The decision itself is taken
 * in `PlanApprovalModal`, which App renders once for both placements.
 */

import { ShowPlanButton } from './AgentReasoningView'

interface MobileHitlBarProps {
  busy: boolean
  /** Opens the approval modal. The decision itself is taken there. */
  onShowPlan: () => void
}

export function MobileHitlBar({ busy, onShowPlan }: MobileHitlBarProps) {
  return (
    <div
      data-testid="mobile-hitl-bar"
      // `md:hidden` is the whole breakpoint rule: Tailwind's `md` is 768px, so this is visible
      // exactly below the width where the agent panel gets its own full-width row.
      className="fixed inset-x-0 bottom-0 z-40 border-t border-ink bg-raised p-3 md:hidden"
    >
      <p className="mb-2 font-mono text-badge uppercase text-pending">Human authorization required</p>
      <ShowPlanButton busy={busy} onShowPlan={onShowPlan} />
    </div>
  )
}
