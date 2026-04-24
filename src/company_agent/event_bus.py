"""Event bus - pub/sub event system inspired by OpenAgents ONM and Paperclip event bus.

Key design decisions:
- Typed events with dot-separated type namespace (e.g., "workflow.task.created")
- Pattern matching for subscriptions using fnmatch-style wildcards
- Async-first implementation
- Events are immutable after creation
- Visibility levels for event routing
"""

import asyncio
import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
from collections import defaultdict


class EventVisibility(str, Enum):
    """Event visibility/destinations."""
    PUBLIC = "public"           # Broadcast to all
    CHANNEL = "channel"         # Channel-scoped
    DIRECT = "direct"           # Specific target only
    INTERNAL = "internal"       # System internal only


@dataclass(frozen=True)
class Event:
    """Universal event envelope - inspired by OpenAgents ONM event model.

    Events are immutable and carry all information about something that happened.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""                           # e.g., "workflow.task.created"
    source: str = ""                         # Who created this event
    target: Optional[str] = None             # Specific target or None for broadcast
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    visibility: EventVisibility = EventVisibility.PUBLIC
    timestamp: float = field(default_factory=lambda: datetime.utcnow().timestamp())

    def with_target(self, target: str) -> "Event":
        """Create a copy with a different target (for forwarding)."""
        return Event(
            id=self.id,
            type=self.type,
            source=self.source,
            target=target,
            payload=self.payload,
            metadata=self.metadata,
            visibility=EventVisibility.DIRECT,
            timestamp=self.timestamp,
        )

    def matches(self, pattern: str) -> bool:
        """Check if event type matches a pattern (fnmatch-style)."""
        return fnmatch.fnmatch(self.type, pattern)


# Handler type: async function that takes an Event
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


@dataclass
class Subscription:
    """A subscription to events."""
    id: str
    pattern: str              # fnmatch pattern, e.g., "workflow.task.*"
    handler: EventHandler
    subscriber: str            # Who subscribed
    active: bool = True


class EventBus:
    """Async pub/sub event bus with pattern matching.

    Inspired by:
    - OpenAgents ONM: unified event envelope
    - Paperclip plugin event bus: typed, namespace-isolated

    Usage:
        bus = EventBus()

        # Subscribe
        sub_id = await bus.subscribe("workflow.task.*", my_handler, subscriber="orchestrator")

        # Publish
        await bus.publish(Event(type="workflow.task.created", source="planner", payload={...}))

        # Unsubscribe
        await bus.unsubscribe(sub_id)
    """

    def __init__(self):
        self._subscriptions: Dict[str, Subscription] = {}
        self._subscriber_patterns: Dict[str, List[str]] = defaultdict(list)  # subscriber → patterns
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        subscriber: str,
    ) -> str:
        """Subscribe to events matching a pattern.

        Returns subscription ID for later unsubscribing.
        """
        async with self._lock:
            sub_id = str(uuid.uuid4())
            sub = Subscription(
                id=sub_id,
                pattern=pattern,
                handler=handler,
                subscriber=subscriber,
            )
            self._subscriptions[sub_id] = sub
            self._subscriber_patterns[subscriber].append(pattern)
            return sub_id

    async def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe by ID. Returns True if found and removed."""
        async with self._lock:
            if subscription_id in self._subscriptions:
                sub = self._subscriptions[subscription_id]
                self._subscriber_patterns[sub.subscriber].remove(sub.pattern)
                del self._subscriptions[subscription_id]
                return True
            return False

    async def unsubscribe_all(self, subscriber: str) -> int:
        """Unsubscribe all handlers for a subscriber. Returns count removed."""
        async with self._lock:
            patterns = self._subscriber_patterns.get(subscriber, [])
            removed = 0
            for pattern in patterns:
                to_remove = [
                    sid for sid, s in self._subscriptions.items()
                    if s.subscriber == subscriber and s.pattern == pattern
                ]
                for sid in to_remove:
                    del self._subscriptions[sid]
                    removed += 1
            if subscriber in self._subscriber_patterns:
                del self._subscriber_patterns[subscriber]
            return removed

    async def publish(self, event: Event) -> List[asyncio.Task]:
        """Publish an event to all matching subscribers.

        Returns list of tasks that were created to handle the event.
        The caller does not need to await these - they run fire-and-forget.
        However, for testing, we track them.
        """
        async with self._lock:
            matching = [
                sub for sub in self._subscriptions.values()
                if sub.active and event.matches(sub.pattern)
            ]

        if not matching:
            return []

        # Also check visibility filtering
        tasks = []
        for sub in matching:
            # Skip DIRECT events not targeted at this subscriber
            if event.visibility == EventVisibility.DIRECT:
                if event.target and event.target != sub.subscriber:
                    continue

            task = asyncio.create_task(self._safe_handle(sub, event))
            tasks.append(task)

        return tasks

    async def _safe_handle(self, sub: Subscription, event: Event):
        """Handle an event, catching any exceptions."""
        try:
            await sub.handler(event)
        except Exception as e:
            # Log but don't crash - one failing handler shouldn't affect others
            import logging
            logging.error(f"Event handler error in {sub.subscriber}: {e}",
                         exc_info=True)

    async def get_subscriptions(self, subscriber: Optional[str] = None) -> List[Subscription]:
        """Get active subscriptions, optionally filtered by subscriber."""
        async with self._lock:
            if subscriber:
                return [s for s in self._subscriptions.values() if s.subscriber == subscriber]
            return list(self._subscriptions.values())

    def clear(self):
        """Clear all subscriptions. For testing only."""
        self._subscriptions.clear()
        self._subscriber_patterns.clear()


# Factory for common event types
def create_event(
    event_type: str,
    source: str,
    payload: Optional[Dict[str, Any]] = None,
    target: Optional[str] = None,
    **metadata,
) -> Event:
    """Convenience factory for creating events with common patterns."""
    return Event(
        type=event_type,
        source=source,
        target=target,
        payload=payload or {},
        metadata=metadata,
    )


# Standard event types (constants for type safety)
class EventTypes:
    """Standard event type constants."""

    # Workflow events
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_APPROVED = "workflow.approved"
    WORKFLOW_REJECTED = "workflow.rejected"

    # Task events
    TASK_CREATED = "workflow.task.created"
    TASK_STARTED = "workflow.task.started"
    TASK_COMPLETED = "workflow.task.completed"
    TASK_FAILED = "workflow.task.failed"
    TASK_OUTPUT = "workflow.task.output"        # Streaming output
    TASK_PROGRESS = "workflow.task.progress"     # Progress update

    # Agent events
    AGENT_REGISTERED = "agent.registered"
    AGENT_UNREGISTERED = "agent.unregistered"
    AGENT_READY = "agent.ready"

    # Request events
    REQUEST_RECEIVED = "request.received"
    REQUEST_PARSED = "request.parsed"

    # Review events
    REVIEW_REQUESTED = "review.requested"
    REVIEW_COMPLETED = "review.completed"

    # Approval events
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"

    # System events
    SYSTEM_ERROR = "system.error"
    SYSTEM_SHUTDOWN = "system.shutdown"
