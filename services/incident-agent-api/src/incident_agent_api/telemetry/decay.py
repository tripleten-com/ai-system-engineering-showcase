"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/telemetry/decay.py
Component:          Exponential Recovery Decay
Purpose:            Models the 4-second e^(-1.8t) return to baseline that follows an authenticated
                    worker completion callback, per the telemetry specification §5.
Interacts With:     None (pure math)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Exponential Decay, Telemetry Math, Deterministic Simulation
Tools:              Python 3.11

Recovery carries no jitter. That is deliberate: the decay curve is the one part of the demo
whose intermediate values are quoted in the documentation, and a jittered curve could not land
on them. Jitter resumes when the run returns to HEALTHY.
"""

import math

from incident_agent_api.telemetry import baseline, chaos
from incident_agent_api.telemetry.baseline import MetricValues
from tripleten_contracts import ScenarioId

DECAY_K = 1.8
DECAY_DURATION_SECONDS = 4.0


def decayed(nominal: float, peak: float, t: float, k: float = DECAY_K) -> float:
    """Returns a metric's value t seconds into the exponential recovery from peak to nominal."""
    return nominal + (peak - nominal) * math.exp(-k * t)


def apply(values: MetricValues, scenario: ScenarioId, t_decay: float) -> MetricValues:
    """Returns the sample with every affected metric placed on its recovery curve at t_decay."""
    profile = chaos.PROFILES[scenario]
    result = dict(values)

    for field_name, peak in profile.recovery_from.items():
        result[field_name] = decayed(baseline.NOMINALS[field_name], peak, t_decay)

    for field_name, held_value in profile.recovery_stepped.items():
        result[field_name] = held_value

    return chaos.normalise(result)
