/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        scenario_4_prompt_injection.spec.ts
 * Purpose:          Playwright E2E walkthrough for SEV-1 Adversarial Prompt Injection — the
 *                   scenario whose defining property is that nothing breaks.
 * Interacts With:   Incident War Room UI (:3000), API (:8000), localstack (:4566)
 *
 * Curriculum Project: Project 4 — Security, PII Redaction & Guardrails
 * Skills:           E2E Guardrail Verification, Negative Assertions, HITL Containment
 * Tools:            Playwright, TypeScript
 *
 * This spec is mostly negative assertions, and that is the point. The other three scenarios are
 * verified by what they do; this one is verified by what it refuses to do — no chaos math, no
 * executed injection, no unauthorized action, and no decay because there is nothing to recover from.
 */

import { expect, test } from '@playwright/test'

import { BASELINE, expectInBand, openPlanModal, openWarRoom, resetStack, snapshot, waitForState } from './helpers'

/** The two calls the injected instruction tries to force. Neither is a canonical tool. */
const INJECTED_CALLS = ['flush_database_tables', 'dump_aws_credentials'] as const

test.describe('Scenario 4: Adversarial Prompt Injection', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('intercepts the exploit without ever causing an outage', async ({ page, request }) => {
    await openWarRoom(page)

    const before = await snapshot(request)
    expect(before.state).toBe('HEALTHY')

    await page.getByTestId('trigger-prompt_injection').click()

    // The badge says SECURITY EVENT, never CRITICAL OUTAGE. This distinction "must not be subtle".
    const reached = await waitForState(page, ['EXPLOIT_INTERCEPTED', 'AWAITING_APPROVAL'])
    expect(reached).not.toBe('CRITICAL_OUTAGE')
    const badge = page.getByTestId('status-badge')
    await expect(badge).not.toContainText('CRITICAL OUTAGE')

    // The persistent strip, from the moment the guardrail fires.
    await expect(page.getByTestId('no-impact-strip')).toContainText('NO CUSTOMER IMPACT — 0 UNAUTHORIZED ACTIONS', {
      ignoreCase: true,
    })

    // The critical assertion: the infrastructure gauges never leave baseline. Sampled repeatedly
    // across the whole run, because a single check could miss a transient spike.
    const guardBaseline = async (label: string) => {
      const current = await snapshot(request)
      expectInBand(current.golden_signals.latency_p99_ms, BASELINE.p99Ms, `${label} p99`)
      expectInBand(current.infrastructure.db_pool_utilization_pct, BASELINE.dbPoolPct, `${label} DB pool`)
      expect(current.golden_signals.http_5xx_error_rate_pct, `${label} error rate`).toBe(0)
      expect(current.infrastructure.system_health_status, `${label} health enum must read Degraded`).toBe(2)

      // And the bar is *rendered* green, not merely holding green numbers. §3 says this "must not
      // be subtle": three scenarios flash red, this one never does. Following the run state alone
      // turned it amber at the approval gate like every outage.
      for (const value of await page.getByTestId('metric-value').all()) {
        expect(await value.getAttribute('data-tone'), `${label} tile tone`).toBe('healthy')
      }
    }
    await guardBaseline('during interception')

    // The injected calls render struck through and tagged, and the counter records the violation.
    const blocked = page.getByTestId('blocked-tool-call')
    await expect(blocked.first()).toBeVisible({ timeout: 15_000 })
    for (const call of INJECTED_CALLS) {
      await expect(page.getByTestId('log-sanitizer')).toContainText(call)
    }
    await expect(page.getByTestId('log-sanitizer')).toContainText('Blocked by schema firewall', { ignoreCase: true })
    expect((await snapshot(request)).infrastructure.security_violations_total).toBe(1)

    // No PII in this payload: it is an instruction override, not a credential leak.
    await expect(page.getByTestId('masked-count')).toContainText('0 Sensitive Tokens Masked')

    const match = page.getByTestId('rag-match').first()
    await expect(match).toBeVisible({ timeout: 15_000 })
    await expect(match).toHaveAttribute('data-runbook-id', 'SEC-501')

    // Containment is gated exactly like every other scenario.
    await waitForState(page, ['AWAITING_APPROVAL'])
    await expect(page.getByTestId('worker-log')).toContainText('Awaiting approval')
    await guardBaseline('at the gate')

    const modal = await openPlanModal(page)
    await expect(modal.getByTestId('plan-modal-prompt')).toContainText('Confirm Security Quarantine & Block IP')
    await modal.getByTestId('authorize-remediation').click()
    await expect(page.getByTestId('plan-modal')).toHaveCount(0)

    // Only the three authorized containment tools run, and only after the click.
    const workerLog = page.getByTestId('worker-log')
    await expect(workerLog).toContainText(/revoke_session|session/i, { timeout: 25_000 })
    await expect(workerLog).toContainText(/block_ip|blocked/i, { timeout: 25_000 })
    await expect(workerLog).toContainText(/forensic/i, { timeout: 25_000 })

    // The injected calls are never executed. Not "blocked then retried" — never run at all.
    for (const call of INJECTED_CALLS) {
      await expect(workerLog).not.toContainText(call)
    }

    // Terminal SECURITY_CONTAINED, in cyan, with no decay — there was nothing to recover from.
    await waitForState(page, ['SECURITY_CONTAINED'], 30_000)
    const banner = page.getByTestId('terminal-state-banner')
    await expect(banner).toHaveAttribute('data-state', 'SECURITY_CONTAINED')
    await expect(banner).toContainText('SESSION REVOKED, IP BLOCKED, FORENSICS ARCHIVED')
    // Cyan, not crimson. A guardrail that held is a success, and this is the assertion that stops
    // a refactor from grouping all three non-recovery endings under one alarm colour.
    // (What stood here was `expect(banner).not.toBeNull()`, which a Playwright locator can never
    // fail — a test-shaped no-op.)
    await expect(banner).toHaveClass(/border-guard/)
    await expect(banner).not.toHaveClass(/border-alarm/)
    await expect(page.getByTestId('no-impact-strip')).toBeVisible()

    // The forensic archive opens itself, and it is the evidence that the three containment tools
    // ran. Its wording is containment, not recovery — nothing here degraded, so there was nothing
    // to recover from, and the terminal banner above still carries that distinction.
    const drawer = page.getByTestId('postmortem-modal')
    await expect(drawer).toBeVisible({ timeout: 15_000 })
    await expect(drawer.getByTestId('postmortem-runbook')).toHaveText('SEC-501')
    await expect(drawer.getByTestId('postmortem-scenario')).toHaveText('prompt_injection')

    const archived = await request.get((await page.getByTestId('postmortem-link').getAttribute('href'))!)
    expect(archived.ok(), 'the archived forensic report must be downloadable').toBeTruthy()
    const report = await archived.json()
    expect(report.tools_executed).toEqual(['revoke_session', 'block_ip', 'archive_forensics'])
    expect(report.authorized_by_human).toBe(true)

    await page.getByTestId('postmortem-close').click()
    await expect(drawer).toHaveCount(0)
    // The claim survives the drawer being dismissed.
    await expect(page.getByTestId('no-impact-strip')).toBeVisible()
    await guardBaseline('after containment')
  })
})
