/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        scenario_3_worker_deadlock.spec.ts
 * Purpose:          Playwright E2E walkthrough for SEV-1 Worker Deadlock & Queue Backpressure.
 * Interacts With:   Incident War Room UI (:3000), API (:8000), localstack (:4566)
 *
 * Curriculum Project: Project 3 — Asynchronous Queues & Cloud Operations
 * Skills:           E2E Incident Simulation, Queue Backpressure, DLQ Verification
 * Tools:            Playwright, TypeScript
 */

import { expect, test } from '@playwright/test'

import { DECAY_MS, decideFromPlanModal, openPlanModal, expectNoRawSecrets, openTechnicalDetails, openWarRoom, resetStack, snapshot, waitForState } from './helpers'

/** The AWS key and the EC2-style hostname from the Scenario 3 log fixtures. */
const RAW_SECRETS = ['wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'ip-10-0-8-12'] as const

test.describe('Scenario 3: Worker Deadlock & Queue Backpressure', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('builds a backlog, quarantines the poison message, and reboots the pool', async ({ page, request }) => {
    await openWarRoom(page)

    await page.getByTestId('trigger-worker_deadlock').click()
    await waitForState(page, ['CRITICAL_OUTAGE', 'AWAITING_APPROVAL'])

    // Backlog climbs and the consumer pool empties. The threshold is well short of the 1,540 peak
    // deliberately — the assertion is that backpressure is building, not that a sampled tick caught
    // the maximum.
    await expect
      .poll(async () => (await snapshot(request)).infrastructure.sqs_active_queue_depth, { timeout: 15_000 })
      .toBeGreaterThan(400)
    expect((await snapshot(request)).infrastructure.active_workers_count).toBe(0)

    await expect(page.getByTestId('masked-count')).toContainText('2 Sensitive Tokens Masked', { timeout: 15_000 })
    const logDetails = await openTechnicalDetails(page, 'log-sanitizer')
    await expect(logDetails).toContainText('[REDACTED: aws_key]')
    await expect(logDetails).toContainText('[REDACTED: hostname]')
    await expectNoRawSecrets(page, RAW_SECRETS)

    const match = page.getByTestId('rag-match').first()
    await expect(match).toBeVisible({ timeout: 15_000 })
    await expect(match).toHaveAttribute('data-runbook-id', 'RB-312')

    await waitForState(page, ['AWAITING_APPROVAL'])
    // The contractual prompt names the action inside the dialog now rather than labelling a button.
    const modal = await openPlanModal(page)
    await expect(modal.getByTestId('plan-modal-prompt')).toContainText('Authorize DLQ Quarantine & Worker Reboot')
    await page.getByTestId('plan-modal-close').click()

    // The poison message is really in `customer-dlq` while the incident is live, so the gauge reads
    // 1 here. It clears when the run reaches HEALTHY, which is why this is asserted at the gate
    // rather than after recovery.
    expect((await snapshot(request)).infrastructure.dlq_message_count).toBe(1)

    await decideFromPlanModal(page, true)

    // Quarantine before reboot: fresh consumers must not pick the poison payload back up, which is
    // the order RB-312's mitigation gives and the order the approval prompt names.
    const workerLog = page.getByTestId('worker-log')
    await expect(workerLog).toContainText(/quarantine|dlq/i, { timeout: 25_000 })
    await expect(workerLog).toContainText(/reboot/i, { timeout: 25_000 })

    // The postmortem drawer opens itself once the run settles. Dismissed straight away so the
    // overlay is not sitting over the telemetry the rest of this spec reads.
    const drawer = page.getByTestId('postmortem-modal')
    await expect(drawer).toBeVisible({ timeout: 25_000 })
    await expect(drawer.getByTestId('postmortem-runbook')).toHaveText('RB-312')
    await page.getByTestId('postmortem-close').click()
    await expect(drawer).toHaveCount(0)

    // Polled for the same reason as Scenario 1: the queue drains and the DLQ clears on the tick
    // that reaches HEALTHY, not on the one that announces it.
    await waitForState(page, ['RECOVERING', 'HEALTHY'])
    await page.waitForTimeout(DECAY_MS)
    await waitForState(page, ['HEALTHY'], 15_000)

    await expect
      .poll(
        async () => {
          const current = await snapshot(request)
          return current.infrastructure.dlq_message_count === 0 && current.infrastructure.active_workers_count > 0
        },
        { timeout: 10_000, intervals: [500] },
      )
      .toBe(true)

    const settled = await snapshot(request)
    // Zero at HEALTHY: the quarantined message is drained when the run completes, per the
    // `dlq_message_count` note in the chaos profile. It held at 1 for the whole incident, which is
    // what the gate assertion above pinned.
    expect(settled.infrastructure.dlq_message_count).toBe(0)
    expect(settled.infrastructure.active_workers_count).toBeGreaterThan(0)
    // The backlog drains; the baseline band for queue depth is single digits, not zero, because the
    // customer workload keeps producing.
    expect(settled.infrastructure.sqs_active_queue_depth).toBeLessThan(20)
  })
})
