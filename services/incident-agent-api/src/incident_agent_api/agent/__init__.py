"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/__init__.py
Component:          Autonomous Agent Package
Purpose:            Re-exports the Project 5 surface: the graph, the guardrail, the tool
                    registry, the offline planner, and the checkpointer lifecycle.
Interacts With:     postgres-vector (:5432), localstack (:4566)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             LangGraph Orchestration, HITL Checkpoints, Module Boundaries
Tools:              LangGraph, Pydantic 2, Python 3.11
"""

from incident_agent_api.agent.checkpointer import (
    close_checkpointer,
    get_checkpointer,
    setup_checkpointer,
    to_psycopg_dsn,
)
from incident_agent_api.agent.graph import (
    AgentRuntime,
    AgentState,
    build_graph,
    resume_command,
    thread_config,
)
from incident_agent_api.agent.guardrails import (
    GuardrailViolationError,
    ValidatedToolCall,
    is_blocked,
    validate_tool_call,
)
from incident_agent_api.agent.mock_llm import RemediationPlan, synthesize_plan
from incident_agent_api.agent.tools import (
    READ_ONLY_DISPATCH,
    RemediationToolNotExecutableError,
    invoke_read_only_tool,
)

__all__ = [
    "READ_ONLY_DISPATCH",
    "AgentRuntime",
    "AgentState",
    "GuardrailViolationError",
    "RemediationPlan",
    "RemediationToolNotExecutableError",
    "ValidatedToolCall",
    "build_graph",
    "close_checkpointer",
    "get_checkpointer",
    "invoke_read_only_tool",
    "is_blocked",
    "resume_command",
    "setup_checkpointer",
    "synthesize_plan",
    "thread_config",
    "to_psycopg_dsn",
    "validate_tool_call",
]
