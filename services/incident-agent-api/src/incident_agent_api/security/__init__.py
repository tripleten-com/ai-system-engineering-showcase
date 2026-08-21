"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/security/__init__.py
Component:          Security & Guardrails Package
Purpose:            Re-exports the inbound sanitization surface (Project 4).
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             PII Sanitization, Module Boundaries
Tools:              Python 3.11
"""

from incident_agent_api.security.sanitizer import SanitizedLog, contains_secret, sanitize, sanitize_all

__all__ = ["SanitizedLog", "contains_secret", "sanitize", "sanitize_all"]
