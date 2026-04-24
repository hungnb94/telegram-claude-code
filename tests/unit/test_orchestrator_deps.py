"""Test workflow orchestration with dependencies."""

import asyncio
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.task_queue import Task, TaskType, TaskStatus, TaskQueue
from company_agent.event_bus import EventBus
from company_agent.agents import AgentRegistry, AgentAdapter, TaskResult
from company_agent.workflow.planner import Planner
from company_agent.workflow.orchestrator import WorkflowOrchestrator


class DummyAdapter(AgentAdapter):
    """Adapter that returns success for test."""
    def __init__(self, name="test", success=True, output="done"):
        super().__init__(name=name)
        self._success = success
        self._output = output

    @property
    def supported_types(self):
        return ["code", "test", "review"]

    async def execute(self, task):
        return TaskResult(success=self._success, output=self._output)


@pytest.fixture
def components():
    """Create fresh orchestrator components."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        temp_path = Path(f.name)

    task_queue = TaskQueue(temp_path)
    event_bus = EventBus()
    registry = AgentRegistry()
    planner = Planner()

    # Register dummy adapters
    registry.register(DummyAdapter("claude"))
    registry.register(DummyAdapter("pytest"))
    registry.register(DummyAdapter("kilocode"))

    orchestrator = WorkflowOrchestrator(
        task_queue=task_queue,
        event_bus=event_bus,
        agent_registry=registry,
        planner=planner,
    )

    yield orchestrator, task_queue

    # Cleanup
    temp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_workflow_with_3_linked_tasks(components):
    """Test that all 3 tasks (code->test->review) execute in order."""
    orchestrator, task_queue = components

    # Create and execute workflow
    workflow = await orchestrator.create_and_execute(
        request="do something",
        context={}
    )

    # Verify workflow completed
    assert workflow.status == TaskStatus.COMPLETED, f"Expected COMPLETED, got {workflow.status}"

    # Verify all 3 tasks ran
    tasks = await task_queue.get_tasks(workflow_id=workflow.id)
    assert len(tasks) == 3, f"Expected 3 tasks, got {len(tasks)}"

    # Check each task type
    code_tasks = [t for t in tasks if t.type == TaskType.CODE]
    test_tasks = [t for t in tasks if t.type == TaskType.TEST]
    review_tasks = [t for t in tasks if t.type == TaskType.REVIEW]

    assert len(code_tasks) == 1
    assert len(test_tasks) == 1
    assert len(review_tasks) == 1

    # All should be completed
    for task in tasks:
        assert task.status == TaskStatus.COMPLETED, f"Task {task.type} was {task.status}, expected COMPLETED"


@pytest.mark.asyncio
async def test_workflow_deps_respected(components):
    """Verify test task waits for code, review waits for test."""
    orchestrator, task_queue = components

    workflow = await orchestrator.create_and_execute(
        request="do something",
        context={}
    )

    tasks = {t.type: t for t in await task_queue.get_tasks(workflow_id=workflow.id)}

    code_task = tasks[TaskType.CODE]
    test_task = tasks[TaskType.TEST]
    review_task = tasks[TaskType.REVIEW]

    # Test task should depend on code task
    assert code_task.id in test_task.deps, f"Test should depend on Code. Deps: {test_task.deps}"

    # Review task should depend on test task
    assert test_task.id in review_task.deps, f"Review should depend on Test. Deps: {review_task.deps}"


@pytest.mark.asyncio
async def test_workflow_sequential_execution(components):
    """Test that tasks execute sequentially in dependency order."""
    orchestrator, task_queue = components

    execution_order = []

    # Patch execute to track order
    original_execute = orchestrator._execute_task

    async def tracked_execute(task):
        execution_order.append(task.type)
        return await original_execute(task)

    orchestrator._execute_task = tracked_execute

    workflow = await orchestrator.create_and_execute(
        request="do something",
        context={}
    )

    # Should execute code first, then test, then review
    assert execution_order == [TaskType.CODE, TaskType.TEST, TaskType.REVIEW], \
        f"Expected [CODE, TEST, REVIEW], got {execution_order}"
