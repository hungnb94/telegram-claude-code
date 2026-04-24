"""Tests for the event bus."""

import asyncio
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.event_bus import EventBus, Event, EventTypes, EventVisibility, create_event


@pytest.mark.asyncio
async def test_event_creation():
    """Test basic event creation."""
    event = Event(type="test.event", source="test")
    assert event.id is not None
    assert event.type == "test.event"
    assert event.source == "test"
    assert event.visibility == EventVisibility.PUBLIC


@pytest.mark.asyncio
async def test_event_matches():
    """Test event pattern matching."""
    event = Event(type="workflow.task.created", source="test")
    assert event.matches("workflow.task.*")
    assert event.matches("workflow.*")
    assert event.matches("*")
    assert not event.matches("other.*")


@pytest.mark.asyncio
async def test_subscribe_and_publish():
    """Test basic subscribe and publish."""
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    sub_id = await bus.subscribe("test.*", handler, "test_subscriber")
    assert sub_id is not None

    await bus.publish(Event(type="test.foo", source="test"))

    # Give handlers time to run
    await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].type == "test.foo"


@pytest.mark.asyncio
async def test_unsubscribe():
    """Test unsubscribe."""
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    sub_id = await bus.subscribe("test.*", handler, "test_subscriber")
    await bus.unsubscribe(sub_id)

    await bus.publish(Event(type="test.foo", source="test"))
    await asyncio.sleep(0.01)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_multiple_subscribers():
    """Test multiple subscribers to same pattern."""
    bus = EventBus()
    received1 = []
    received2 = []

    async def handler1(event):
        received1.append(event)

    async def handler2(event):
        received2.append(event)

    await bus.subscribe("test.*", handler1, "sub1")
    await bus.subscribe("test.*", handler2, "sub2")

    await bus.publish(Event(type="test.foo", source="test"))
    await asyncio.sleep(0.01)

    assert len(received1) == 1
    assert len(received2) == 1


@pytest.mark.asyncio
async def test_wildcard_patterns():
    """Test wildcard pattern matching."""
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    await bus.subscribe("workflow.task.*", handler, "test")

    await bus.publish(Event(type="workflow.task.created", source="planner"))
    await asyncio.sleep(0.01)
    assert len(received) == 1

    await bus.publish(Event(type="workflow.task.completed", source="orchestrator"))
    await asyncio.sleep(0.01)
    assert len(received) == 2

    # Should not match
    await bus.publish(Event(type="workflow.started", source="orchestrator"))
    await asyncio.sleep(0.01)
    assert len(received) == 2  # Still 2


@pytest.mark.asyncio
async def test_create_event_factory():
    """Test the create_event factory function."""
    event = create_event(
        EventTypes.TASK_CREATED,
        source="planner",
        payload={"task_id": "123"},
    )
    assert event.type == "workflow.task.created"
    assert event.source == "planner"
    assert event.payload["task_id"] == "123"


@pytest.mark.asyncio
async def test_direct_target():
    """Test DIRECT visibility targeting."""
    bus = EventBus()
    received_by_a = []
    received_by_b = []

    async def handler_a(event):
        received_by_a.append(event)

    async def handler_b(event):
        received_by_b.append(event)

    await bus.subscribe("test.*", handler_a, "agent_a")
    await bus.subscribe("test.*", handler_b, "agent_b")

    # Publish to specific target
    event = Event(
        type="test.direct",
        source="orchestrator",
        target="agent_a",
        visibility=EventVisibility.DIRECT,
    )
    await bus.publish(event)
    await asyncio.sleep(0.01)

    assert len(received_by_a) == 1
    assert len(received_by_b) == 0  # Should not receive
