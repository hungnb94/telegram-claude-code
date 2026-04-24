"""Planner - converts user requests into task plans.

Inspired by OpenAgents' orchestration approach and Paperclip's issue-based task planning.
The planner analyzes a request and creates a graph of tasks with dependencies.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from company_agent.task_queue import Task, TaskType


@dataclass
class TaskPlan:
    """A plan containing multiple tasks with dependencies."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    tasks: List[Task] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)  # Shared plan context
    created_at: float = 0.0

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def topological_order(self) -> List[Task]:
        """Return tasks in dependency order."""
        result = []
        visited = set()

        def visit(task: Task):
            if task.id in visited:
                return
            visited.add(task.id)
            for dep_id in task.deps:
                dep = self.get_task(dep_id)
                if dep:
                    visit(dep)
            result.append(task)

        for task in self.tasks:
            visit(task)

        return result


class Planner:
    """Converts natural language requests into task plans.

    The planner analyzes what needs to be done and creates a graph of tasks
    with appropriate dependencies. For MVP, this is a simple rule-based planner.

    Future: Could use an LLM to generate more sophisticated plans.
    """

    def __init__(self):
        pass

    def create_plan(
        self,
        request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        """Create a task plan from a user request.

        Args:
            request: The user's request (natural language)
            context: Additional context (project path, user info, etc.)

        Returns:
            TaskPlan with tasks and dependencies
        """
        context = context or {}
        plan = TaskPlan(
            name=self._extract_name(request),
            description=request,
            context=context,
        )

        # Analyze request to determine what tasks to create
        tasks = self._create_tasks_from_request(request, context)
        plan.tasks = tasks

        return plan

    def _extract_name(self, request: str) -> str:
        """Extract a short name from the request."""
        # Take first 50 chars, strip common words
        name = request.strip()[:50]
        if len(request) > 50:
            name += "..."
        return name

    def _create_tasks_from_request(
        self,
        request: str,
        context: Dict[str, Any],
    ) -> List[Task]:
        """Create tasks based on the request analysis.

        Default pipeline: code → test → review
        """
        tasks = []
        request_lower = request.lower()

        # Create task IDs for dependency references
        code_task_id = str(uuid.uuid4())
        test_task_id = str(uuid.uuid4())
        review_task_id = str(uuid.uuid4())

        project_path = context.get("project_path", "")

        # Always create a code task first
        code_task = Task(
            id=code_task_id,
            type=TaskType.CODE,
            agent="claude",
            status=context.get("initial_status", TaskType.CODE).value,
            payload={
                "prompt": self._build_code_prompt(request, context),
                "files": context.get("files", []),
                "context": {
                    "request": request,
                    **context,
                },
            },
            workflow_id=context.get("workflow_id"),
            priority=10,  # High priority for code
        )
        tasks.append(code_task)

        # Check if this is a test-only request
        if self._is_test_request(request_lower):
            test_task = Task(
                id=test_task_id,
                type=TaskType.TEST,
                agent="pytest",
                deps=[code_task_id],
                payload={
                    "path": context.get("test_path", "tests/"),
                    "context": {"request": request, **context},
                },
                workflow_id=context.get("workflow_id"),
                priority=8,
            )
            tasks.append(test_task)
            return tasks

        # Check if this is a review-only request
        if self._is_review_request(request_lower):
            review_task = Task(
                id=review_task_id,
                type=TaskType.REVIEW,
                agent="kilocode",
                deps=[code_task_id],
                payload={
                    "prompt": self._build_review_prompt(request, context),
                    "files": context.get("files", []),
                    "context": {"request": request, **context},
                },
                workflow_id=context.get("workflow_id"),
                priority=6,
            )
            tasks.append(review_task)
            return tasks

        # Full pipeline: code → test → review
        # Test task depends on code
        test_task = Task(
            id=test_task_id,
            type=TaskType.TEST,
            agent="pytest",
            deps=[code_task_id],
            payload={
                "path": context.get("test_path", "tests/"),
                "context": {"request": request, **context},
            },
            workflow_id=context.get("workflow_id"),
            priority=8,
        )
        tasks.append(test_task)

        # Review task depends on test
        review_task = Task(
            id=review_task_id,
            type=TaskType.REVIEW,
            agent="kilocode",
            deps=[test_task_id],
            payload={
                "prompt": self._build_review_prompt(request, context),
                "files": context.get("files", []),
                "context": {"request": request, **context},
            },
            workflow_id=context.get("workflow_id"),
            priority=6,
        )
        tasks.append(review_task)

        return tasks

    def _is_test_request(self, request_lower: str) -> bool:
        """Check if request is specifically for running tests."""
        test_keywords = [
            "run test", "run tests", "execute test",
            "test the", "test my", "test this",
            "pytest", "jest", "run the tests",
        ]
        return any(kw in request_lower for kw in test_keywords) and not any(
            kw in request_lower for kw in ["implement", "create", "build", "add feature"]
        )

    def _is_review_request(self, request_lower: str) -> bool:
        """Check if request is specifically for code review."""
        review_keywords = [
            "review", "check code", "analyze", "audit",
            "look at", "assess", "evaluate",
        ]
        return any(kw in request_lower for kw in review_keywords) and not any(
            kw in request_lower for kw in ["implement", "create", "build", "fix"]
        )

    def _build_code_prompt(self, request: str, context: Dict[str, Any]) -> str:
        """Build the prompt for the code task."""
        prompt = request

        # Add context about project
        if context.get("project_type"):
            prompt = f"[{context['project_type']} project] {prompt}"

        # Add instructions for TDD if enabled
        if context.get("tdd_mode"):
            prompt = f"{prompt}\n\nFollow TDD: write tests first, then implement."

        return prompt

    def _build_review_prompt(self, request: str, context: Dict[str, Any]) -> str:
        """Build the prompt for the review task."""
        return f"Review the code changes for: {request}\n\nFocus on: correctness, security, performance, and maintainability."


# Pattern matching for request types
REQUEST_PATTERNS = {
    "feature": [
        "implement", "create", "build", "add", "develop",
        "feature", " functionality",
    ],
    "fix": [
        "fix", "bug", "repair", "patch", "correct",
        "resolve", "issue", "problem",
    ],
    "refactor": [
        "refactor", "restructure", "reorganize", "improve",
        "clean up", "optimize",
    ],
    "test": [
        "test", "spec", "verify", "check",
    ],
    "review": [
        "review", "audit", "assess", "evaluate", "analyze",
    ],
}


def classify_request(request: str) -> str:
    """Classify a request into a category."""
    request_lower = request.lower()

    for category, patterns in REQUEST_PATTERNS.items():
        if any(p in request_lower for p in patterns):
            return category

    return "general"
