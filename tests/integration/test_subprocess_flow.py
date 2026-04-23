import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY

import pytest

from telegram_claude_agent.task_queue import TaskQueue
from telegram_claude_agent.claude_subprocess import ClaudeSubprocess


@pytest.mark.integration
class TestSubprocessFlow:
    def test_claude_subprocess_runs_with_mocked_popen(self, tmp_path, capsys):
        """Test ClaudeSubprocess.run() with mocked subprocess — verifies streaming flow."""
        tasks_file = tmp_path / "tasks.json"
        q = TaskQueue(tasks_file)
        q.enqueue({"message_id": "1", "text": "test task", "chat_id": "c1"})

        output_lines = []
        error_lines = []

        def output_cb(line):
            output_lines.append(line)

        def error_cb(line):
            error_lines.append(line)

        mock_process = MagicMock()
        mock_process.stdout = iter(["line1\n", "line2\n", "line3\n"])
        mock_process.returncode = 0
        mock_process.wait = MagicMock(return_value=0)

        with patch("subprocess.Popen", return_value=mock_process):
            subprocess = ClaudeSubprocess(str(tmp_path), timeout_minutes=5)
            result = subprocess.run("test task", output_cb, error_cb)

        assert result is True
        assert len(output_lines) == 3
        assert "line1" in output_lines[0]

    def test_queue_persistence_across_operations(self, tmp_path):
        """Test queue state persists correctly across enqueue/dequeue/complete."""
        tasks_file = tmp_path / "tasks.json"
        q = TaskQueue(tasks_file)

        q.enqueue({"message_id": "1", "text": "task1", "chat_id": "c1"})
        q.enqueue({"message_id": "2", "text": "task2", "chat_id": "c1"})
        q.dequeue()  # removes task1, now running
        q.complete("1", success=True)

        # Simulate new queue instance (reload from disk)
        q2 = TaskQueue(tasks_file)
        assert q2.peek()["message_id"] == "2"  # task2 still pending
        assert q2.has_running_task() is False
        assert len(q2._load()["completed"]) == 1
        assert q2._load()["completed"][0]["success"] is True

    def test_subprocess_timeout_handling(self, tmp_path):
        """Test that timeout kills process and returns False."""
        import subprocess

        output_lines = []
        error_lines = []

        def output_cb(line):
            output_lines.append(line)

        def error_cb(line):
            error_lines.append(line)

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.wait = MagicMock(side_effect=subprocess.TimeoutExpired("cmd", 1))
        mock_process.terminate = MagicMock()
        mock_process.wait_after_kill = MagicMock()

        with patch("subprocess.Popen", return_value=mock_process):
            subprocess_runner = ClaudeSubprocess(str(tmp_path), timeout_minutes=1)
            result = subprocess_runner.run("task", output_cb, error_cb)

        assert result is False
        mock_process.terminate.assert_called_once()

    def test_full_task_lifecycle(self, tmp_path):
        """Test enqueue → dequeue → complete → verify state."""
        tasks_file = tmp_path / "tasks.json"
        q = TaskQueue(tasks_file)

        msg_id = q.enqueue({"message_id": "1", "text": "hello", "chat_id": "456"})
        assert msg_id == "1"

        task = q.dequeue()
        assert task["message_id"] == "1"

        q.complete("1", success=True)
        data = q._load()
        assert data["running"] is None
        assert len(data["completed"]) == 1
        assert data["completed"][0]["success"] is True
        assert data["completed"][0]["message_id"] == "1"
