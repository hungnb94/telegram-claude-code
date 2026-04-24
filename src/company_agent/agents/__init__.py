"""Agent adapters - bridge between the task queue and actual AI tools.

Each adapter wraps a specific AI tool (Claude, KiloCode, Pytest, etc.)
and provides a uniform interface for the orchestrator.
"""

from company_agent.agents.base import AgentAdapter, AgentRegistry, Skill, TaskResult
from company_agent.agents.claude_adapter import ClaudeAdapter
from company_agent.agents.kilocode_adapter import KiloCodeAdapter
from company_agent.agents.pytest_adapter import PytestAdapter

__all__ = [
    "AgentAdapter",
    "AgentRegistry",
    "Skill",
    "TaskResult",
    "ClaudeAdapter",
    "KiloCodeAdapter",
    "PytestAdapter",
]
