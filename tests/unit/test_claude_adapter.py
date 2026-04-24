"""Tests for ClaudeAdapter - especially the execute_streaming method."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.agents.claude_adapter import ClaudeAdapter
from company_agent.task_queue import Task, TaskType


class MockProcess:
    """Mock subprocess.Process that uses returncode (no poll())."""
    def __init__(self, returncode=None):
        self._returncode = returncode
        self.poll_called = False
        self.returncode_checks = 0

    def poll(self):
        self.poll_called = True
        return self._returncode

    @property
    def returncode(self):
        self.returncode_checks += 1
        return self._returncode

    async def wait(self):
        self._returncode = 0
        return 0


class TestClaudeAdapterReturncode:
    """Verify execute_streaming uses returncode, not poll()."""

    @pytest.mark.asyncio
    async def test_execute_streaming_checks_returncode_not_poll(self):
        """execute_streaming should use .returncode, not .poll().

        Python 3.9's asyncio.subprocess.Process doesn't have .poll(),
        so we must use .returncode property instead.
        """
        adapter = ClaudeAdapter(project_path="/tmp")
        task = Task(type=TaskType.CODE, agent="claude", payload={"prompt": "hello"})

        mock_process = MockProcess(returncode=0)

        reads = [b"output\n", b""]  # Second read returns empty to exit loop

        async def mock_os_read(fd, size):
            await asyncio.sleep(0.001)
            return reads.pop(0) if reads else b""

        with patch('os.openpty', return_value=(123, 456)):
            with patch('os.close'):
                with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_create:
                    mock_create.return_value = mock_process

                    with patch('asyncio.get_event_loop') as mock_loop:
                        mock_loop_instance = MagicMock()
                        mock_loop.return_value = mock_loop_instance
                        mock_loop_instance.run_in_executor = lambda *args: asyncio.sleep(0.001)

                        with patch('os.read', mock_os_read):
                            async def callback(text):
                                pass

                            result = await adapter.execute_streaming(task, callback)

        # Key assertion: poll() should NOT be called
        assert not mock_process.poll_called, ".poll() must NOT be called on Python 3.9+"

        # returncode should be checked
        assert mock_process.returncode_checks > 0, ".returncode should be checked"


class TestClaudeAdapterExecute:
    """Test the regular execute method."""

    @pytest.mark.asyncio
    async def test_execute_returns_task_result(self):
        """Test that execute returns a proper TaskResult."""
        adapter = ClaudeAdapter(project_path="/tmp")
        task = Task(type=TaskType.CODE, agent="claude", payload={"prompt": "hello"})

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_create:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b"output", b""))
            mock_create.return_value = mock_process

            result = await adapter.execute(task)

            assert result.success is True
            assert "output" in result.output

    @pytest.mark.asyncio
    async def test_execute_handles_error(self):
        """Test that execute properly handles errors."""
        adapter = ClaudeAdapter(project_path="/tmp")
        task = Task(type=TaskType.CODE, agent="claude", payload={"prompt": "hello"})

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_create:
            mock_process = MagicMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
            mock_create.return_value = mock_process

            result = await adapter.execute(task)

            assert result.success is False
            assert result.error is not None

    @pytest.mark.asyncio
    async def test_execute_no_prompt_returns_error(self):
        """Test that execute with no prompt returns error."""
        adapter = ClaudeAdapter(project_path="/tmp")
        task = Task(type=TaskType.CODE, agent="claude", payload={})  # No prompt

        result = await adapter.execute(task)

        assert result.success is False
        assert "No prompt provided" in result.error
