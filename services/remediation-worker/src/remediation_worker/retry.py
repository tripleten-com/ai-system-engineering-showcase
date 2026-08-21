"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/retry.py
Component:          Retry Budget & Backoff Schedule
Purpose:            The canonical delivery budget and jittered exponential backoff, expressed
                    once so the consumer and the tests agree on it.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Exponential Backoff, Jitter, Dead-Letter Queueing
Tools:              Python 3.11

The schedule is fixed by coding-standards-and-guide.md §5.3 and every number in it is load
bearing:

* **3 deliveries maximum**, matching `maxReceiveCount=3` on both source queues. The 4th delivery
  is the DLQ redrive, performed by SQS rather than by this worker.
* **Backoff 2s, 4s, 8s** with **±25% jitter**. Worst case elapsed is therefore
  `(2 + 4 + 8) × 1.25 = 17.5s`.
* **Visibility timeout 30s** on both source queues, which must exceed that 17.5s — otherwise SQS
  redelivers a message that is still being worked on, and two workers remediate the same
  incident concurrently. Stage 1 verification and the smoke suite both assert the 30s.

Backoff is applied by *shortening the message's visibility*, not by sleeping. Sleeping would
hold the worker's single poll loop hostage for up to 8 seconds and stall every other job;
`ChangeMessageVisibility` hands the wait to SQS and returns the loop to the queue immediately.
"""

import random

# Matches maxReceiveCount=3 on customer-jobs and remediation-jobs.
MAX_DELIVERIES = 3

BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MULTIPLIER = 2.0
JITTER_FRACTION = 0.25

# Asserted by the unit suite against the queues' configured visibility timeout.
VISIBILITY_TIMEOUT_SECONDS = 30


def backoff_seconds(delivery: int, rng: random.Random | None = None) -> float:
    """Returns the jittered delay before delivery `delivery` is retried (1-based).

    Jitter is uniform ±25% around the exponential term. Its purpose is desynchronisation: a
    fleet of workers all failing on the same downstream dependency would otherwise retry in
    lockstep and arrive as a thundering herd — the very failure mode Scenario 2 is about.
    """
    if delivery < 1:
        raise ValueError(f"delivery is 1-based, got {delivery}")
    generator = rng if rng is not None else random.Random()
    nominal = BACKOFF_BASE_SECONDS * (BACKOFF_MULTIPLIER ** (delivery - 1))
    return nominal * (1.0 + generator.uniform(-JITTER_FRACTION, JITTER_FRACTION))


def worst_case_elapsed_seconds() -> float:
    """Returns the longest total time the retry budget can consume, jitter included."""
    nominal = sum(
        BACKOFF_BASE_SECONDS * (BACKOFF_MULTIPLIER**index) for index in range(MAX_DELIVERIES)
    )
    return nominal * (1.0 + JITTER_FRACTION)


def budget_exhausted(delivery: int) -> bool:
    """True when this delivery was the last one the budget allows."""
    return delivery >= MAX_DELIVERIES
