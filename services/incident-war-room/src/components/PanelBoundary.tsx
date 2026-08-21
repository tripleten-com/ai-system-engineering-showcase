/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        PanelBoundary.tsx
 * Purpose:          Per-panel error boundary, so one malformed payload degrades one column
 *                   instead of blanking the page mid-demo.
 * Interacts With:   App.tsx, every column component
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Error Boundaries, Graceful Degradation
 * Tools:            React 18, Tailwind CSS
 *
 * `spa-design-guidelines.md` §8 asks for exactly this: a malformed `RAG_MATCH` payload should
 * degrade one panel to `Panel unavailable`, not take down the page. React error boundaries are
 * still class components — there is no hook equivalent — which is why this one file departs from
 * the function-component convention everywhere else.
 *
 * It deliberately does not retry or reset itself. The stream keeps running, so the next well-formed
 * frame is a second away; a boundary that remounted on every render would loop on a persistently
 * bad payload and flash the panel instead of holding a readable message.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Panel } from './ui'

interface PanelBoundaryProps {
  /** Named in the fallback, so a viewer can tell which column degraded. */
  title: string
  children: ReactNode
}

interface PanelBoundaryState {
  message: string | null
}

export class PanelBoundary extends Component<PanelBoundaryProps, PanelBoundaryState> {
  state: PanelBoundaryState = { message: null }

  static getDerivedStateFromError(error: unknown): PanelBoundaryState {
    return { message: error instanceof Error ? error.message : 'Unknown rendering error' }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Logged rather than reported: there is no error backend in this stack, and a silent catch
    // would make a reproducible frontend bug invisible during development.
    console.error(`[war-room] panel "${this.props.title}" failed to render`, error, info.componentStack)
  }

  render(): ReactNode {
    const { message } = this.state
    if (message === null) return this.props.children

    return (
      <Panel title={this.props.title} tone="alarm" testId="panel-unavailable">
        <p className="font-sans text-body text-text-secondary">
          Panel unavailable — this column could not render the last payload. The rest of the war
          room is unaffected.
        </p>
        <p className="mt-2 font-mono text-log text-text-secondary">{message}</p>
      </Panel>
    )
  }
}
