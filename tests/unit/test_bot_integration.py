"""TDD tests for Bot integration with WorkflowOrchestrator.

These tests ensure Bot correctly wires up the company_agent workflow components.
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_config():
    return {
        "telegram_bot_token": "test_token",
        "claude_code_project_path": "/tmp/test_project",
        "poll_interval_seconds": 5,
        "task_timeout_minutes": 30,
        "allowed_telegram_usernames": ["testuser"],
        "review": {"enabled": False},
    }


@pytest.fixture
def bot_instance(mock_config):
    """Create a Bot instance with mocked TelegramBot."""
    import sys
    from pathlib import Path as P
    
    # Ensure company_agent is importable
    src_path = str(P(__file__).parent.parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    
    with patch("telegram_claude_agent.bot.TelegramBot"):
        from telegram_claude_agent.bot import Bot
        bot = Bot(mock_config)
        return bot


class TestBotWorkflowIntegration:
    """Test Bot correctly integrates with company_agent workflow components."""

    def test_bot_has_legacy_queue(self, bot_instance):
        """Bot should have legacy TaskQueue for Telegram message queueing."""
        from telegram_claude_agent.task_queue import TaskQueue
        assert hasattr(bot_instance, "queue")
        assert isinstance(bot_instance.queue, TaskQueue)

    def test_bot_has_workflow_queue(self, bot_instance):
        """Bot should have WorkflowTaskQueue for Orchestrator."""
        from company_agent.task_queue import TaskQueue as WorkflowTaskQueue
        assert hasattr(bot_instance, "_workflow_queue")
        assert isinstance(bot_instance._workflow_queue, WorkflowTaskQueue)

    def test_bot_has_event_bus(self, bot_instance):
        """Bot should have EventBus for workflow events."""
        from company_agent.event_bus import EventBus
        assert hasattr(bot_instance, "_event_bus")
        assert isinstance(bot_instance._event_bus, EventBus)

    def test_bot_has_clarification_manager(self, bot_instance):
        """Bot should have ClarificationManager for interactive mode."""
        from company_agent.workflow.clarification import ClarificationManager
        assert hasattr(bot_instance, "_clarification_manager")
        assert isinstance(bot_instance._clarification_manager, ClarificationManager)

    def test_bot_has_agent_registry(self, bot_instance):
        """Bot should have AgentRegistry with adapters."""
        from company_agent.agents import AgentRegistry
        assert hasattr(bot_instance, "_agent_registry")
        assert isinstance(bot_instance._agent_registry, AgentRegistry)

    def test_bot_has_orchestrator(self, bot_instance):
        """Bot should have WorkflowOrchestrator wired to workflow_queue."""
        from company_agent.workflow.orchestrator import WorkflowOrchestrator
        assert hasattr(bot_instance, "_orchestrator")
        assert isinstance(bot_instance._orchestrator, WorkflowOrchestrator)
        # Orchestrator should use _workflow_queue, not legacy queue
        assert bot_instance._orchestrator.task_queue is bot_instance._workflow_queue

    def test_orchestrator_has_clarification_manager(self, bot_instance):
        """Orchestrator should have ClarificationManager for interactive mode."""
        assert hasattr(bot_instance._orchestrator, "clarification_manager")
        assert bot_instance._orchestrator.clarification_manager is bot_instance._clarification_manager

    def test_clarification_manager_has_event_bus(self, bot_instance):
        """ClarificationManager should be wired to EventBus."""
        assert bot_instance._clarification_manager._event_bus is bot_instance._event_bus

    def test_workflow_queue_is_different_from_legacy_queue(self, bot_instance):
        """Workflow queue and legacy queue should be separate instances."""
        assert bot_instance.queue is not bot_instance._workflow_queue


class TestLegacyQueueStillWorks:
    """Ensure legacy queue operations still work for backward compatibility."""

    def test_legacy_queue_enqueue(self, bot_instance):
        """Legacy queue enqueue should work."""
        task = {"message_id": "123", "text": "test", "chat_id": "456"}
        result = bot_instance.queue.enqueue(task)
        assert result == "123"
        assert bot_instance.queue.peek()["message_id"] == "123"

    def test_legacy_queue_set_running(self, bot_instance):
        """Legacy queue set_running should work."""
        task = {"message_id": "123", "text": "test", "chat_id": "456"}
        bot_instance.queue.set_running(task)
        assert bot_instance.queue.has_running_task() is True

    def test_legacy_queue_complete(self, bot_instance):
        """Legacy queue complete should work."""
        task = {"message_id": "123", "text": "test", "chat_id": "456"}
        bot_instance.queue.set_running(task)
        bot_instance.queue.complete("123", success=True)
        assert bot_instance.queue.has_running_task() is False
