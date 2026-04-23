import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from poc_dev_flow_agent.task_queue import TaskQueue


@pytest.mark.unit
class TestTaskQueue:
    def test_enqueue_returns_message_id(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        msg_id = q.enqueue({"message_id": "123", "text": "hello", "chat_id": "456"})
        assert msg_id == "123"

    def test_peek_returns_first_task(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.enqueue({"message_id": "1", "text": "first", "chat_id": "c1"})
        q.enqueue({"message_id": "2", "text": "second", "chat_id": "c2"})
        task = q.peek()
        assert task["message_id"] == "1"

    def test_dequeue_moves_to_running(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.enqueue({"message_id": "1", "text": "task", "chat_id": "c1"})
        task = q.dequeue()
        assert task["message_id"] == "1"
        data = q._load()
        assert data["running"] is not None
        assert len(data["pending"]) == 0

    def test_complete_moves_to_completed(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.enqueue({"message_id": "1", "text": "task", "chat_id": "c1"})
        q.dequeue()
        q.complete("1", success=True)
        data = q._load()
        assert data["running"] is None
        assert len(data["completed"]) == 1
        assert data["completed"][0]["success"] is True

    def test_mark_failed_sets_success_false(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.enqueue({"message_id": "1", "text": "task", "chat_id": "c1"})
        q.dequeue()
        q.mark_failed("1")
        data = q._load()
        assert data["completed"][0]["success"] is False

    def test_has_running_task(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        assert q.has_running_task() is False
        q.enqueue({"message_id": "1", "text": "task", "chat_id": "c1"})
        q.dequeue()
        assert q.has_running_task() is True

    def test_set_running(self, tmp_path):
        q = TaskQueue(tmp_path / "tasks.json")
        q.enqueue({"message_id": "1", "text": "first", "chat_id": "c1"})
        q.enqueue({"message_id": "2", "text": "second", "chat_id": "c2"})
        q.set_running({"message_id": "2", "text": "force second", "chat_id": "c2"})
        data = q._load()
        assert data["running"]["message_id"] == "2"
        assert len(data["pending"]) == 1
        assert data["pending"][0]["message_id"] == "1"
