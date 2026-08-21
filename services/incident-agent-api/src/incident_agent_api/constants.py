"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/constants.py
Component:          Service Readiness Constants
Purpose:            Non-configurable values the readiness probe checks against, derived from
                    the shared contracts so the names cannot drift from the worker's.
Interacts With:     localstack (:4566), postgres-vector (:5432)

Curriculum Project:  Cross-cutting — Modular Ports & Contract Design
Skills:             Contract-First Design, Readiness Gating
Tools:              Python 3.11
"""

from tripleten_contracts import BucketName, QueueName, RunbookId

# Derived, never retyped: adding a queue to the contract adds it to the readiness probe.
REQUIRED_QUEUES = [q.value for q in QueueName]
REQUIRED_BUCKETS = [b.value for b in BucketName]

# The demo seeds exactly one runbook per scenario; fewer means ingestion has not finished.
MINIMUM_SEEDED_RUNBOOKS = len(RunbookId)
