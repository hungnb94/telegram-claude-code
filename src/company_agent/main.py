"""Main entry point for the AI Company Agent.

This wires together all components:
- EventBus
- TaskQueue
- AgentRegistry with adapters
- WorkflowOrchestrator
- TelegramHandler / TelegramReporter

Usage:
    python -m company_agent.main

Or run directly:
    python -m company_agent
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from company_agent import config
from company_agent.event_bus import EventBus
from company_agent.task_queue import TaskQueue
from company_agent.agents import AgentRegistry, ClaudeAdapter, KiloCodeAdapter, PytestAdapter
from company_agent.workflow import WorkflowOrchestrator
from company_agent.handlers.telegram_handler import TelegramHandler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Application:
    """Main application that wires all components together."""

    def __init__(self):
        self.cfg = config.get_config()
        self.event_bus = EventBus()
        self.task_queue = TaskQueue(self.cfg.tasks_file)
        self.agent_registry = AgentRegistry()
        self.orchestrator: WorkflowOrchestrator = None
        self.telegram_handler: TelegramHandler = None
        self._running = False

    def setup_agents(self):
        """Set up agent adapters."""
        # Claude agent
        claude = ClaudeAdapter(
            project_path=self.cfg.claude.project_path,
        )
        self.agent_registry.register(claude)

        # KiloCode agent (for reviews)
        kilocode = KiloCodeAdapter(
            project_path=self.cfg.kilocode.project_path if hasattr(self.cfg.kilocode, 'project_path') else None,
        )
        self.agent_registry.register(kilocode)

        # Pytest agent (for tests)
        pytest_adapter = PytestAdapter(
            project_path=self.cfg.pytest.project_path if hasattr(self.cfg.pytest, 'project_path') else self.cfg.claude.project_path,
            test_paths=self.cfg.pytest.test_paths if hasattr(self.cfg.pytest, 'test_paths') else ["tests/"],
        )
        self.agent_registry.register(pytest_adapter)

        logger.info(f"Registered agents: {[a.name for a in self.agent_registry.get_all()]}")

    def setup_orchestrator(self):
        """Set up the workflow orchestrator."""
        self.orchestrator = WorkflowOrchestrator(
            task_queue=self.task_queue,
            event_bus=self.event_bus,
            agent_registry=self.agent_registry,
        )

    def setup_telegram(self):
        """Set up Telegram handler."""
        self.telegram_handler = TelegramHandler(
            config=self.cfg.telegram,
            orchestrator=self.orchestrator,
            event_bus=self.event_bus,
        )

    async def start(self):
        """Start the application."""
        logger.info("Starting AI Company Agent...")

        self.setup_agents()
        self.setup_orchestrator()
        self.setup_telegram()

        self._running = True

        # Start Telegram handler
        telegram_task = asyncio.create_task(self.telegram_handler.start())

        logger.info("AI Company Agent started!")

        # Wait until shutdown
        while self._running:
            await asyncio.sleep(1)

        telegram_task.cancel()

    def stop(self):
        """Stop the application."""
        logger.info("Stopping AI Company Agent...")
        self._running = False


def main():
    """Main entry point."""
    app = Application()

    def signal_handler(sig, frame):
        app.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("AI Company Agent stopped.")


if __name__ == "__main__":
    main()
