"""ClarificationManager - interactive question/answer between agents and users.

When an agent encounters ambiguity, it can pause and ask the user a question.
The workflow waits until the user answers before continuing.

Architecture:
  Agent asks → ClarificationManager → EventBus → Reporter → User (Telegram)
                ↑                                           ↓
                └──────────── answer_received ←─────────────┘
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Coroutine


class ClarificationType(str, Enum):
    """Types of clarification requests."""
    CHOICE = "choice"           # Pick from options
    TEXT = "text"              # Free-form text answer
    CONFIRM = "confirm"         # Yes/No confirmation
    CODE_CHOICE = "code_choice" # Pick code/approach


class ClarificationRequested(Exception):
    """Raised by an agent when it needs user input to proceed.

    This is raised during task execution to pause the workflow
    and wait for the user to answer the question.
    """
    def __init__(
        self,
        question: str,
        options: Optional[List[str]] = None,
        clarification_type: ClarificationType = ClarificationType.CHOICE,
        default: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 300,
    ):
        super().__init__(question)
        self.question = question
        self.options = options
        self.clarification_type = clarification_type
        self.default = default
        self.context = context or {}
        self.timeout_seconds = timeout_seconds
        self._answer: Optional[str] = None

    def set_answer(self, answer: str):
        """Set the answer (called by ClarificationManager)."""
        self._answer = answer

    @property
    def answer(self) -> Optional[str]:
        return self._answer


@dataclass
class Clarification:
    """A clarification question/request."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    asker: str = ""             # Which agent is asking
    question: str = ""
    clarification_type: ClarificationType = ClarificationType.CHOICE
    options: Optional[List[str]] = None  # For CHOICE type
    default: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)  # Extra info
    answer: Optional[str] = None
    answered_by: Optional[str] = None
    answered_at: Optional[float] = None
    status: str = "pending"     # pending, answered, timeout, cancelled
    created_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    timeout_seconds: float = 300  # 5 min default


class ClarificationManager:
    """Manages clarification questions between agents and users.

    Key features:
    - Create questions with type (choice/text/confirm)
    - Wait for async answer from user
    - Timeout support
    - EventBus integration for notifications
    - Multiple pending questions support
    """

    def __init__(self, event_bus=None):
        self._clarifications: Dict[str, Clarification] = {}
        self._awaiters: Dict[str, asyncio.Future] = {}
        self._event_bus = event_bus
        self._listeners: List[Callable[[Clarification], Coroutine]] = []

    def create(
        self,
        question: str,
        options: Optional[List[str]] = None,
        task_id: str = "",
        asker: str = "",
        clarification_type: ClarificationType = ClarificationType.CHOICE,
        default: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 300,
    ) -> Clarification:
        """Create a new clarification question.

        Args:
            question: The question to ask
            options: Available options (for CHOICE type)
            task_id: Which task is asking
            asker: Which agent is asking
            clarification_type: Type of question
            default: Default answer if user doesn't respond
            context: Additional context
            timeout_seconds: How long to wait

        Returns:
            The created Clarification
        """
        clar = Clarification(
            question=question,
            options=options,
            task_id=task_id,
            asker=asker,
            clarification_type=clarification_type,
            default=default,
            context=context or {},
            timeout_seconds=timeout_seconds,
        )
        self._clarifications[clar.id] = clar

        # Notify listeners
        for listener in self._listeners:
            try:
                asyncio.create_task(listener(clar))
            except Exception:
                pass

        return clar

    async def answer(self, clarification_id: str, answer: str, answered_by: str) -> bool:
        """Submit an answer to a clarification.

        Args:
            clarification_id: ID of the clarification
            answer: The answer
            answered_by: Who answered

        Returns:
            True if found and updated, False if not found
        """
        clar = self._clarifications.get(clarification_id)
        if not clar or clar.status != "pending":
            return False

        clar.answer = answer
        clar.answered_by = answered_by
        clar.answered_at = datetime.utcnow().timestamp()
        clar.status = "answered"

        # Wake up the waiter
        if clarification_id in self._awaiters:
            future = self._awaiters.pop(clarification_id)
            if not future.done():
                future.set_result(answer)

        return True

    async def wait_for_answer(
        self,
        clarification_id: str,
        timeout: float = 300,
    ) -> Optional[str]:
        """Wait for an answer to a clarification.

        This is called by the agent to pause and wait.

        Args:
            clarification_id: Which clarification to wait for
            timeout: Max seconds to wait

        Returns:
            The answer string, or None if timeout/cancelled
        """
        clar = self._clarifications.get(clarification_id)
        if not clar:
            return None

        if clar.status != "pending":
            return clar.answer

        future = asyncio.get_event_loop().create_future()
        self._awaiters[clarification_id] = future

        try:
            answer = await asyncio.wait_for(future, timeout=timeout)
            return answer
        except asyncio.TimeoutError:
            clar.status = "timeout"
            return None

    def get_pending(self) -> List[Clarification]:
        """Get all pending clarifications."""
        return [c for c in self._clarifications.values() if c.status == "pending"]

    def get_pending_for_task(self, task_id: str) -> List[Clarification]:
        """Get pending clarifications for a specific task."""
        return [c for c in self.get_pending() if c.task_id == task_id]

    def get_by_id(self, clarification_id: str) -> Optional[Clarification]:
        """Get a clarification by ID."""
        return self._clarifications.get(clarification_id)

    def cancel(self, clarification_id: str) -> bool:
        """Cancel a pending clarification."""
        clar = self._clarifications.get(clarification_id)
        if not clar or clar.status != "pending":
            return False
        clar.status = "cancelled"
        if clarification_id in self._awaiters:
            future = self._awaiters.pop(clarification_id)
            if not future.done():
                future.set_result(None)
        return True

    def add_listener(self, listener: Callable[[Clarification], Coroutine]):
        """Add a listener for new clarifications."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Clarification], Coroutine]):
        """Remove a clarification listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def get_answer_for_task(
        self,
        task_id: str,
        question: str,
        options: Optional[List[str]] = None,
        asker: str = "",
        timeout: float = 300,
    ) -> Optional[str]:
        """Ask a question and wait for answer - convenience method.

        This combines create() + wait_for_answer() in one call.

        Args:
            task_id: Which task is asking
            question: Question to ask
            options: Options if applicable
            asker: Which agent asks
            timeout: Max wait time

        Returns:
            The answer or None
        """
        clar = self.create(
            question=question,
            options=options,
            task_id=task_id,
            asker=asker,
        )
        return await self.wait_for_answer(clar.id, timeout=timeout)
