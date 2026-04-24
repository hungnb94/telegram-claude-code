"""Task queue with JSON persistence and dependency tracking.

Extends the original TaskQueue with:
- Dependency-based execution
- Task graph support
- Async operations
- More task states
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class TaskStatus(str, Enum):
    """Task lifecycle states."""
    PENDING = "pending"      # Created, waiting for deps
    WAITING = "waiting"      # Waiting for dependencies to complete
    RUNNING = "running"      # Currently executing
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"        # Finished with error
    CANCELLED = "cancelled"  # Manually cancelled
    SKIPPED = "skipped"       # Skipped due to dependency failure


class TaskType(str, Enum):
    """Types of tasks agents can execute."""
    PLAN = "plan"
    CODE = "code"
    TEST = "test"
    REVIEW = "review"
    REFACTOR = "refactor"
    DEPLOY = "deploy"


@dataclass
class TaskResult:
    """Result of a task execution."""
    success: bool
    output: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0


def _safe_status(status_str: str) -> TaskStatus:
    """Safely convert string to TaskStatus, defaulting to PENDING if invalid."""
    try:
        return TaskStatus(status_str)
    except ValueError:
        return TaskStatus.PENDING


@dataclass
class Task:
    """A task in the queue - represents a unit of work for an agent."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: TaskType = TaskType.CODE
    agent: str = ""                    # Which agent adapter to use (e.g., "claude", "pytest")
    status: TaskStatus = TaskStatus.PENDING
    deps: List[str] = field(default_factory=list)  # Task IDs this depends on
    payload: Dict[str, Any] = field(default_factory=dict)  # {prompt, files, context...}
    result: Optional[TaskResult] = None
    workflow_id: Optional[str] = None  # Parent workflow ID
    created_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    updated_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 2
    priority: int = 0  # Higher = more priority

    def can_run(self, completed_tasks: Set[str]) -> bool:
        """Check if this task can run (all deps completed)."""
        if self.status not in (TaskStatus.PENDING, TaskStatus.WAITING):
            return False
        return all(dep in completed_tasks for dep in self.deps)

    def mark_running(self):
        """Mark task as running."""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.utcnow().timestamp()
        self.updated_at = self.started_at

    def mark_completed(self, result: TaskResult):
        """Mark task as completed with result."""
        self.status = TaskStatus.COMPLETED
        self.result = result
        now = datetime.utcnow().timestamp()
        self.completed_at = now
        self.updated_at = now
        if self.started_at:
            result.duration_seconds = self.completed_at - self.started_at

    def mark_failed(self, error: str, result: Optional[TaskResult] = None):
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.retry_count += 1
        now = datetime.utcnow().timestamp()
        self.updated_at = now
        if result:
            self.result = result
            self.result.error = error
        else:
            self.result = TaskResult(success=False, error=error)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, Enum) else self.type,
            "agent": self.agent,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "deps": self.deps,
            "payload": self.payload,
            "result": {
                "success": self.result.success,
                "output": self.result.output,
                "error": self.result.error,
                "metadata": self.result.metadata,
                "duration_seconds": self.result.duration_seconds,
            } if self.result else None,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Deserialize from dict."""
        result_data = data.get("result")
        result = None
        if result_data:
            result = TaskResult(
                success=result_data["success"],
                output=result_data.get("output", ""),
                error=result_data.get("error"),
                metadata=result_data.get("metadata", {}),
                duration_seconds=result_data.get("duration_seconds", 0.0),
            )

        return cls(
            id=data["id"],
            type=TaskType(data.get("type", "code")),
            agent=data.get("agent", ""),
            status=_safe_status(data.get("status", "pending")),
            deps=data.get("deps", []),
            payload=data.get("payload", {}),
            result=result,
            workflow_id=data.get("workflow_id"),
            created_at=data.get("created_at", datetime.utcnow().timestamp()),
            updated_at=data.get("updated_at", datetime.utcnow().timestamp()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 2),
            priority=data.get("priority", 0),
        )


@dataclass
class Workflow:
    """A workflow - collection of tasks with shared context."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: TaskStatus = TaskStatus.PENDING
    tasks: List[str] = field(default_factory=list)  # Task IDs in order
    context: Dict[str, Any] = field(default_factory=dict)  # Shared workflow context
    created_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    updated_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    completed_at: Optional[float] = None
    result: Optional[TaskResult] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "tasks": self.tasks,
            "context": self.context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "result": {
                "success": self.result.success,
                "output": self.result.output,
                "error": self.result.error,
                "metadata": self.result.metadata,
                "duration_seconds": self.result.duration_seconds,
            } if self.result else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        result_data = data.get("result")
        result = None
        if result_data:
            result = TaskResult(
                success=result_data["success"],
                output=result_data.get("output", ""),
                error=result_data.get("error"),
                metadata=result_data.get("metadata", {}),
                duration_seconds=result_data.get("duration_seconds", 0.0),
            )
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            status=_safe_status(data.get("status", "pending")),
            tasks=data.get("tasks", []),
            context=data.get("context", {}),
            created_at=data.get("created_at", datetime.utcnow().timestamp()),
            updated_at=data.get("updated_at", datetime.utcnow().timestamp()),
            completed_at=data.get("completed_at"),
            result=result,
        )


class TaskQueue:
    """Async task queue with JSON persistence and dependency tracking.

    Extends the original TaskQueue with:
    - Dependency-based execution
    - Workflow support
    - Async operations
    - More task states
    """

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, Task] = {}
        self._workflows: Dict[str, Workflow] = {}
        self._dirty = False
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        """Load tasks from JSON file."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path) as f:
                    data = json.load(f)
                    self._tasks = {
                        k: Task.from_dict(v) for k, v in data.get("tasks", {}).items()
                    }
                    self._workflows = {
                        k: Workflow.from_dict(v) for k, v in data.get("workflows", {}).items()
                    }
            except (json.JSONDecodeError, KeyError) as e:
                # Corrupted file - start fresh
                self._tasks = {}
                self._workflows = {}

    async def _save(self):
        """Save tasks to JSON file (only if dirty)."""
        if not self._dirty:
            return
        data = {
            "tasks": {k: v.to_dict() for k, v in self._tasks.items()},
            "workflows": {k: v.to_dict() for k, v in self._workflows.items()},
        }
        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)
        self._dirty = False

    # === Workflow methods ===

    async def create_workflow(self, workflow: Workflow) -> Workflow:
        """Create a new workflow."""
        async with self._lock:
            self._workflows[workflow.id] = workflow
            self._dirty = True
            await self._save()
        return workflow

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Get a workflow by ID."""
        return self._workflows.get(workflow_id)

    async def update_workflow(self, workflow: Workflow):
        """Update a workflow."""
        async with self._lock:
            workflow.updated_at = datetime.utcnow().timestamp()
            self._workflows[workflow.id] = workflow
            self._dirty = True
            await self._save()

    async def get_workflows(self, status: Optional[TaskStatus] = None) -> List[Workflow]:
        """Get all workflows, optionally filtered by status."""
        workflows = list(self._workflows.values())
        if status:
            workflows = [w for w in workflows if w.status == status]
        return sorted(workflows, key=lambda w: w.created_at)

    # === Task methods ===

    async def create_task(self, task: Task) -> Task:
        """Create a new task."""
        async with self._lock:
            self._tasks[task.id] = task
            self._dirty = True
            await self._save()
        return task

    async def create_tasks(self, tasks: List[Task]) -> List[Task]:
        """Create multiple tasks at once (atomic)."""
        async with self._lock:
            for task in tasks:
                self._tasks[task.id] = task
            self._dirty = True
            await self._save()
        return tasks

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    async def update_task(self, task: Task):
        """Update a task."""
        async with self._lock:
            task.updated_at = datetime.utcnow().timestamp()
            self._tasks[task.id] = task
            self._dirty = True
            await self._save()

    async def get_tasks(
        self,
        status: Optional[TaskStatus] = None,
        workflow_id: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> List[Task]:
        """Get tasks filtered by various criteria."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        if workflow_id:
            tasks = [t for t in tasks if t.workflow_id == workflow_id]
        if agent:
            tasks = [t for t in tasks if t.agent == agent]
        return sorted(tasks, key=lambda t: (-t.priority, t.created_at))

    async def get_runnable_tasks(self) -> List[Task]:
        """Get tasks that are ready to run (all deps completed)."""
        completed = {
            tid for tid, t in self._tasks.items()
            if t.status == TaskStatus.COMPLETED
        }
        pending = await self.get_tasks(status=TaskStatus.PENDING)
        return [t for t in pending if t.can_run(completed)]

    async def get_next_runnable(self) -> Optional[Task]:
        """Get the highest priority runnable task."""
        runnable = await self.get_runnable_tasks()
        return runnable[0] if runnable else None

    async def claim_task(self, task_id: str, worker: str) -> Optional[Task]:
        """Atomically claim a task for execution (sets to RUNNING).

        Returns None if task doesn't exist or isn't claimable.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status != TaskStatus.PENDING:
                return None

            # Check deps
            completed = {
                tid for tid, t in self._tasks.items()
                if t.status == TaskStatus.COMPLETED
            }
            if not task.can_run(completed):
                return None

            task.status = TaskStatus.RUNNING
            task.started_at = datetime.utcnow().timestamp()
            task.updated_at = task.started_at
            self._dirty = True
            await self._save()
        return task

    async def complete_task(self, task_id: str, result: TaskResult):
        """Mark a task as completed."""
        task = self._tasks.get(task_id)
        if task:
            task.mark_completed(result)
            self._dirty = True
            await self._save()

    async def fail_task(self, task_id: str, error: str):
        """Mark a task as failed."""
        task = self._tasks.get(task_id)
        if task:
            task.mark_failed(error)
            self._dirty = True
            await self._save()

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task. Returns True if cancelled."""
        task = self._tasks.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.WAITING):
            task.status = TaskStatus.CANCELLED
            task.updated_at = datetime.utcnow().timestamp()
            self._dirty = True
            await self._save()
            return True
        return False

    async def get_task_stats(self) -> Dict[str, int]:
        """Get count of tasks by status."""
        stats = {status.value: 0 for status in TaskStatus}
        for task in self._tasks.values():
            status_str = task.status.value if isinstance(task.status, Enum) else str(task.status)
            stats[status_str] = stats.get(status_str, 0) + 1
        return stats

    async def clear_completed(self, older_than_hours: float = 24):
        """Clear completed tasks older than specified hours."""
        import time
        cutoff = time.time() - (older_than_hours * 3600)
        async with self._lock:
            to_remove = [
                tid for tid, t in self._tasks.items()
                if t.status == TaskStatus.COMPLETED and t.completed_at and t.completed_at < cutoff
            ]
            for tid in to_remove:
                del self._tasks[tid]
            if to_remove:
                self._dirty = True
                await self._save()
        return len(to_remove)
