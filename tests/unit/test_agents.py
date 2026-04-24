"""Tests for agent adapters."""

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.agents.base import AgentAdapter, AgentRegistry, Skill, TaskResult
from company_agent.agents.claude_adapter import ClaudeAdapter
from company_agent.agents.pytest_adapter import PytestAdapter
from company_agent.task_queue import Task, TaskType


class MockAgent(AgentAdapter):
    """A mock agent adapter for testing."""

    def __init__(self):
        super().__init__("mock")
        self.executed = []

    @property
    def supported_types(self):
        return ["code", "test"]

    async def execute(self, task):
        self.executed.append(task)
        return TaskResult(success=True, output=f"executed: {task.payload.get('prompt', '')}")


class TestAgentAdapter:
    """Tests for base agent adapter."""

    def test_adapter_has_name(self):
        adapter = MockAgent()
        assert adapter.name == "mock"

    def test_supports_streaming_default(self):
        adapter = MockAgent()
        assert adapter.supports_streaming() is False

    @pytest.mark.asyncio
    async def test_list_skills_default(self):
        adapter = MockAgent()
        skills = await adapter.list_skills()
        # Default returns empty list
        assert isinstance(skills, list)


class TestAgentRegistry:
    """Tests for agent registry."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_register_adapter(self):
        adapter = MockAgent()
        self.registry.register(adapter)
        assert self.registry.get("mock") is adapter

    def test_unregister_adapter(self):
        adapter = MockAgent()
        self.registry.register(adapter)
        assert self.registry.unregister("mock") is True
        assert self.registry.get("mock") is None

    def test_get_by_type(self):
        adapter = MockAgent()
        self.registry.register(adapter)

        adapters = self.registry.get_by_type("code")
        assert len(adapters) == 1
        assert adapters[0].name == "mock"

    def test_get_by_type_none(self):
        adapters = self.registry.get_by_type("nonexistent")
        assert len(adapters) == 0

    def test_get_default_for_type(self):
        adapter = MockAgent()
        self.registry.register(adapter)

        default = self.registry.get_default_for_type("code")
        assert default is adapter

    def test_get_default_for_type_none(self):
        default = self.registry.get_default_for_type("nonexistent")
        assert default is None


class TestTaskResult:
    """Tests for TaskResult dataclass."""

    def test_task_result_success(self):
        result = TaskResult(success=True, output="hello")
        assert result.success is True
        assert result.output == "hello"
        assert result.error is None

    def test_task_result_failure(self):
        result = TaskResult(success=False, error="something broke")
        assert result.success is False
        assert result.error == "something broke"

    def test_task_result_to_dict(self):
        result = TaskResult(success=True, output="test", metadata={"key": "value"})
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "test"
        assert d["metadata"] == {"key": "value"}


class TestSkill:
    """Tests for Skill dataclass."""

    def test_skill_basic(self):
        skill = Skill(name="code", description="Write code")
        assert skill.name == "code"
        assert skill.category == "general"

    def test_skill_with_params(self):
        skill = Skill(
            name="implement",
            description="Implement a feature",
            category="code",
            parameters={"prompt": "str - what to implement"},
        )
        assert skill.parameters["prompt"] == "str - what to implement"
