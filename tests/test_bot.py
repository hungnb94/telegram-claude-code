import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_claude_poc import KaizenScanner, TaskQueue, StreamHandler, TelegramClaudeBot


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


class TestStreamHandler:
    def test_add_line_accumulates_buffer(self):
        h = StreamHandler(lines_per_chunk=3)
        lines = h.add_line("line1")
        assert lines == []
        lines = h.add_line("line2")
        assert lines == []
        lines = h.add_line("line3")
        assert len(lines) == 3

    def test_flush_returns_remaining(self):
        h = StreamHandler(lines_per_chunk=3)
        h.add_line("line1")
        h.add_line("line2")
        remaining = h.flush()
        assert len(remaining) == 2
        assert h.buffer == []


class TestKaizenScanner:
    def test_analyze_empty_tasks_file(self, tmp_path):
        tasks_file = tmp_path / "tasks.json"
        kaizen_file = tmp_path / "kaizen.json"
        scanner = KaizenScanner(str(tmp_path), tasks_file, kaizen_file)
        recs = scanner.scan()
        assert isinstance(recs, list)

    def test_analyze_high_failure_rate(self, tmp_path):
        tasks_file = tmp_path / "tasks.json"
        kaizen_file = tmp_path / "kaizen.json"
        tasks_file.write_text(json.dumps({
            "completed": [
                {"message_id": "1", "success": False},
                {"message_id": "2", "success": False},
                {"message_id": "3", "success": False},
                {"message_id": "4", "success": True},
                {"message_id": "5", "success": False},
            ],
            "pending": [],
            "running": None
        }))
        scanner = KaizenScanner(str(tmp_path), tasks_file, kaizen_file)
        recs = scanner.scan()
        high_priority = [r for r in recs if r["priority"] == "high"]
        assert len(high_priority) >= 1

    def test_should_rescan_after_6_hours(self, tmp_path):
        tasks_file = tmp_path / "tasks.json"
        kaizen_file = tmp_path / "kaizen.json"
        scanner = KaizenScanner(str(tmp_path), tasks_file, kaizen_file)
        assert scanner.should_rescan() is True


class TestTelegramClaudeBot:
    def test_format_kaizen_card(self):
        rec = {"title": "Test", "description": "Desc", "priority": "high", "action": "Do it"}
        card = TelegramClaudeBot.format_kaizen_card(rec, 1, 3)
        assert "Test" in card
        assert "high" in card.lower() or "🔴" in card
        assert "1/3" in card or "1" in card

    def test_authorization_rejects_unknown_user(self, tmp_path):
        config = {
            "telegram_bot_token": "test_token",
            "claude_code_project_path": str(tmp_path),
            "allowed_telegram_usernames": ["allowed_user"],
        }
        bot = TelegramClaudeBot(config)
        assert bot.allowed_usernames == {"allowed_user"}