"""Tests for the task queue."""

import asyncio
import json
import tempfile
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.task_queue import (
    TaskQueue, Task, TaskType, TaskStatus, TaskResult,
    Workflow,
)


@pytest.fixture
def temp_file():
    """Create a temporary file for task queue storage."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def queue(temp_file):
    """Create a task queue with temporary storage."""
    return TaskQueue(temp_file)


def test_task_with_deps():
    """Test tasks with dependencies."""
    task1 = Task(type=TaskType.CODE, agent="claude")
    task2 = Task(type=TaskType.TEST, agent="pytest", deps=[task1.id])

    assert not task2.can_run(set())  # No deps completed
    assert task2.can_run({task1.id})  # Dep completed


@pytest.mark.asyncio
async def test_create_task(queue):
    """Test creating a task."""
    task = Task(
        type=TaskType.CODE,
        agent="claude",
        payload={"prompt": "hello world"},
    )
    created = await queue.create_task(task)

    assert created.id is not None
    assert created.status == TaskStatus.PENDING
    assert created.payload["prompt"] == "hello world"


@pytest.mark.asyncio
async def test_get_task(queue):
    """Test getting a task by ID."""
    task = Task(type=TaskType.CODE, agent="claude")
    await queue.create_task(task)

    retrieved = await queue.get_task(task.id)
    assert retrieved is not None
    assert retrieved.id == task.id


@pytest.mark.asyncio
async def test_claim_task(queue):
    """Test claiming a task."""
    task = Task(type=TaskType.CODE, agent="claude")
    await queue.create_task(task)

    # Cannot claim if deps not met
    task_with_dep = Task(type=TaskType.TEST, agent="pytest", deps=[task.id])
    await queue.create_task(task_with_dep)

    claimed = await queue.claim_task(task_with_dep.id, "worker")
    assert claimed is None  # Cannot claim - deps not met

    # Claim task without deps
    claimed = await queue.claim_task(task.id, "worker")
    assert claimed is not None
    assert claimed.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_complete_task(queue):
    """Test completing a task."""
    task = Task(type=TaskType.CODE, agent="claude")
    await queue.create_task(task)

    await queue.claim_task(task.id, "worker")

    result = TaskResult(success=True, output="hello")
    await queue.complete_task(task.id, result)

    updated = await queue.get_task(task.id)
    assert updated.status == TaskStatus.COMPLETED
    assert updated.result.success


@pytest.mark.asyncio
async def test_fail_task(queue):
    """Test failing a task."""
    task = Task(type=TaskType.CODE, agent="claude")
    await queue.create_task(task)

    await queue.claim_task(task.id, "worker")

    await queue.fail_task(task.id, "Something went wrong")

    updated = await queue.get_task(task.id)
    assert updated.status == TaskStatus.FAILED
    assert updated.retry_count == 1


@pytest.mark.asyncio
async def test_get_runnable_tasks(queue):
    """Test getting runnable tasks (deps met)."""
    task1 = Task(type=TaskType.CODE, agent="claude")
    task2 = Task(type=TaskType.TEST, agent="pytest", deps=[task1.id])
    task3 = Task(type=TaskType.REVIEW, agent="kilocode", deps=[task1.id])

    await queue.create_tasks([task1, task2, task3])

    # Initially only task1 is runnable
    runnable = await queue.get_runnable_tasks()
    assert len(runnable) == 1
    assert runnable[0].id == task1.id

    # After task1 completes, task2 and task3 become runnable
    task1.status = TaskStatus.COMPLETED
    await queue.update_task(task1)

    runnable = await queue.get_runnable_tasks()
    assert len(runnable) == 2


@pytest.mark.asyncio
async def test_workflow_crud(queue):
    """Test workflow creation and retrieval."""
    workflow = Workflow(name="Test Workflow")
    created = await queue.create_workflow(workflow)

    assert created.id is not None
    assert created.status == TaskStatus.PENDING

    retrieved = await queue.get_workflow(workflow.id)
    assert retrieved.id == workflow.id


@pytest.mark.asyncio
async def test_task_serialization(queue):
    """Test that tasks serialize and deserialize correctly."""
    task = Task(
        type=TaskType.CODE,
        agent="claude",
        payload={"prompt": "test", "files": ["a.py", "b.py"]},
    )
    await queue.create_task(task)

    retrieved = await queue.get_task(task.id)
    assert retrieved.type == TaskType.CODE
    assert retrieved.payload["prompt"] == "test"
    assert retrieved.payload["files"] == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_task_stats(queue):
    """Test task statistics."""
    tasks = [
        Task(type=TaskType.CODE, agent="claude"),
        Task(type=TaskType.CODE, agent="claude"),
        Task(type=TaskType.TEST, agent="pytest"),
    ]
    await queue.create_tasks(tasks)

    # Mark one as completed
    tasks[0].status = TaskStatus.COMPLETED
    await queue.update_task(tasks[0])

    stats = await queue.get_task_stats()
    assert stats["pending"] == 2
    assert stats["completed"] == 1
    assert stats["running"] == 0
