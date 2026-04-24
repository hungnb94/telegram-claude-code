"""Tests for ClaudeAdapter clarification detection."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from company_agent.agents.claude_adapter import ClaudeAdapter
from company_agent.task_queue import Task, TaskType
from company_agent.workflow.clarification import ClarificationRequested, ClarificationType


class TestClarificationDetection:
    """Test _detect_clarification raises ClarificationRequested for ambiguous prompts."""

    @pytest.fixture
    def adapter(self):
        return ClaudeAdapter(project_path="/tmp")

    def _make_task(self, prompt: str, context: dict = None) -> Task:
        return Task(
            id="test-task-1",
            type=TaskType.CODE,
            agent="claude",
            payload={"prompt": prompt, "context": context or {}},
        )

    @pytest.mark.unit
    def test_detect_database_clarification(self, adapter):
        """Should raise ClarificationRequested when prompt mentions database options."""
        with pytest.raises(ClarificationRequested) as exc_info:
            adapter._detect_clarification(
                "build an API that uses postgresql or mongodb",
                {"chat_id": "123"}
            )
        assert exc_info.value.question == "Which database should I use?"
        assert exc_info.value.options == ["PostgreSQL", "SQLite", "MongoDB", "Let me decide later"]
        assert exc_info.value.clarification_type == ClarificationType.CHOICE
        assert exc_info.value.context == {"chat_id": "123"}

    @pytest.mark.unit
    def test_detect_framework_clarification(self, adapter):
        """Should raise ClarificationRequested for framework ambiguity."""
        with pytest.raises(ClarificationRequested) as exc_info:
            adapter._detect_clarification(
                "create a web app with react or vue.js",
                {"chat_id": "456"}
            )
        assert "framework" in exc_info.value.question.lower()
        assert exc_info.value.context["chat_id"] == "456"

    @pytest.mark.unit
    def test_detect_rest_or_graphql(self, adapter):
        """Should raise ClarificationRequested for REST vs GraphQL."""
        with pytest.raises(ClarificationRequested) as exc_info:
            adapter._detect_clarification(
                "design a REST or GraphQL API",
                {}
            )
        assert "api" in exc_info.value.question.lower() or "api style" in exc_info.value.question.lower()

    @pytest.mark.unit
    def test_no_clarification_needed(self, adapter):
        """Should NOT raise when prompt is unambiguous."""
        # Should not raise
        adapter._detect_clarification(
            "fix the bug in user authentication",
            {"chat_id": "789"}
        )

    @pytest.mark.unit
    def test_context_passed_through(self, adapter):
        """ClarificationRequested should carry the full context including chat_id."""
        context = {"chat_id": "999", "project_path": "/my/project", "user": "testuser"}
        with pytest.raises(ClarificationRequested) as exc_info:
            adapter._detect_clarification("use microservices or monolith", context)
        assert exc_info.value.context == context

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_execute_raises_on_ambiguous_prompt(self, adapter):
        """execute() should propagate ClarificationRequested from _detect_clarification."""
        task = self._make_task(
            "build something with postgresql or sqlite",
            {"chat_id": "123"}
        )

        # Mock the subprocess to never run
        with pytest.raises(ClarificationRequested) as exc_info:
            await adapter.execute(task)
        assert exc_info.value.context["chat_id"] == "123"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_execute_does_not_raise_on_clear_prompt(self, adapter):
        """execute() should NOT raise ClarificationRequested for clear prompts."""
        task = self._make_task(
            "fix the login bug in auth.py",
            {"chat_id": "123"}
        )

        # Mock subprocess to succeed
        mock_result = AsyncMock()
        mock_result.communicate = AsyncMock(return_value=(b"done", b""))
        mock_result.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_result):
            result = await adapter.execute(task)
            # Should complete without clarification
            assert result.success is True

    @pytest.mark.unit
    def test_clarification_type_is_choice(self, adapter):
        """All detected clarifications should be CHOICE type."""
        patterns = [
            ("which database", {}),
            ("which framework", {}),
            ("rest or graphql", {}),
            ("microservices", {}),
            ("jwt or session", {}),
        ]
        for prompt, context in patterns:
            with pytest.raises(ClarificationRequested) as exc_info:
                adapter._detect_clarification(prompt, context)
            assert exc_info.value.clarification_type == ClarificationType.CHOICE
