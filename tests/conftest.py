"""Shared pytest fixtures and configuration."""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def sample_task():
    """A sample task for testing."""
    from company_agent.task_queue import Task, TaskType
    return Task(
        type=TaskType.CODE,
        agent="claude",
        payload={"prompt": "Hello, world!"},
    )


@pytest.fixture
def sample_workflow():
    """A sample workflow for testing."""
    from company_agent.task_queue import Workflow
    return Workflow(name="Test Workflow")
