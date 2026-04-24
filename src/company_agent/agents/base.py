"""Base agent adapter - abstract interface for all agent adapters.

Inspired by Paperclip's ServerAdapterModule and OpenAgents adapter pattern.
Each adapter wraps a specific AI tool and provides a uniform interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Type

from company_agent.task_queue import Task


@dataclass
class Skill:
    """A skill/ability that an agent can perform."""
    name: str
    description: str
    category: str = "general"
    parameters: Optional[Dict[str, str]] = None  # param_name -> description


@dataclass
class TaskResult:
    """Result of a task execution.

    This is a lightweight result object used by adapters.
    For persistence, the TaskQueue stores its own result.
    """
    success: bool
    output: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
            "duration_seconds": self.duration_seconds,
        }


class AgentAdapter(ABC):
    """Abstract base class for all agent adapters.

    Each adapter wraps a specific AI tool (Claude, KiloCode, Pytest, etc.)
    and provides a uniform interface for the orchestrator.

    Adapters are responsible for:
    - Converting a Task into the appropriate tool invocation
    - Handling streaming output if supported
    - Returning a TaskResult with success/failure and output

    Inspired by:
    - Paperclip's ServerAdapterModule interface
    - OpenAgents adapter polling workspace pattern
    """

    def __init__(self, name: str):
        self.name = name
        self._skills: Optional[List[Skill]] = None

    @property
    @abstractmethod
    def supported_types(self) -> List[str]:
        """Return list of task types this adapter supports.

        Examples: ["code", "test", "review", "refactor"]
        """
        ...

    @abstractmethod
    async def execute(self, task: Task) -> TaskResult:
        """Execute a task synchronously.

        Args:
            task: The task to execute

        Returns:
            TaskResult with success status, output, and metadata
        """
        ...

    def supports_streaming(self) -> bool:
        """Whether this adapter supports output streaming.

        If True, the orchestrator may call execute_streaming instead.
        """
        return False

    async def execute_streaming(
        self,
        task: Task,
        callback: Callable[[str], Coroutine[Any, Any, None]],
    ) -> TaskResult:
        """Execute a task with streaming output.

        The callback is called with chunks of output as they arrive.
        Default implementation falls back to execute() if not supported.

        Args:
            task: The task to execute
            callback: Async function called with each output chunk

        Returns:
            TaskResult with final success status and full output
        """
        if not self.supports_streaming():
            return await self.execute(task)

        raise NotImplementedError(
            f"{self.name} claims to support streaming but execute_streaming not implemented"
        )

    async def list_skills(self) -> List[Skill]:
        """Discover available skills for this agent.

        Returns a list of Skill objects describing what this agent can do.
        May cache results after first call.
        """
        if self._skills is None:
            self._skills = await self._discover_skills()
        return self._skills

    async def _discover_skills(self) -> List[Skill]:
        """Subclasses can override to provide dynamic skill discovery."""
        return []

    async def test_connection(self) -> bool:
        """Test if the underlying tool is available and working.

        Returns True if the tool can be invoked.
        """
        return True

    async def get_metadata(self) -> Dict[str, Any]:
        """Get metadata about this adapter."""
        return {
            "name": self.name,
            "supported_types": self.supported_types,
            "supports_streaming": self.supports_streaming(),
        }


class AgentRegistry:
    """Registry of available agent adapters.

    Provides adapter lookup by name or supported task type.
    This is a simple in-memory registry - can be extended for
    dynamic plugin loading (like Paperclip's plugin system).
    """

    def __init__(self):
        self._adapters: Dict[str, AgentAdapter] = {}
        self._type_index: Dict[str, List[str]] = {}  # type -> adapter names

    def register(self, adapter: AgentAdapter) -> None:
        """Register an adapter."""
        self._adapters[adapter.name] = adapter

        # Index by supported type
        for t in adapter.supported_types:
            if t not in self._type_index:
                self._type_index[t] = []
            if adapter.name not in self._type_index[t]:
                self._type_index[t].append(adapter.name)

    def unregister(self, name: str) -> bool:
        """Unregister an adapter by name."""
        if name not in self._adapters:
            return False

        adapter = self._adapters[name]

        # Remove from type index
        for t in adapter.supported_types:
            if t in self._type_index:
                self._type_index[t] = [n for n in self._type_index[t] if n != name]

        del self._adapters[name]
        return True

    def get(self, name: str) -> Optional[AgentAdapter]:
        """Get an adapter by name."""
        return self._adapters.get(name)

    def get_by_type(self, task_type: str) -> List[AgentAdapter]:
        """Get all adapters that support a given task type."""
        names = self._type_index.get(task_type, [])
        return [self._adapters[n] for n in names if n in self._adapters]

    def get_all(self) -> List[AgentAdapter]:
        """Get all registered adapters."""
        return list(self._adapters.values())

    def get_default_for_type(self, task_type: str) -> Optional[AgentAdapter]:
        """Get the default adapter for a task type (first registered)."""
        adapters = self.get_by_type(task_type)
        return adapters[0] if adapters else None
