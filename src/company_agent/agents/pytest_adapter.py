"""Pytest agent adapter - runs tests using pytest.

Used by TestAgent in the workflow pipeline.
"""

import asyncio
import re
import os
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from company_agent.agents.base import AgentAdapter, Skill, TaskResult
from company_agent.task_queue import Task


class PytestAdapter(AgentAdapter):
    """Adapter for running pytest.

    Executes tests and reports pass/fail results.
    Supports streaming output for real-time test progress.
    """

    def __init__(
        self,
        project_path: Optional[str] = None,
        test_paths: Optional[List[str]] = None,
        timeout_seconds: int = 300,
        strict_types: bool = True,
    ):
        super().__init__(name="pytest")
        self.project_path = project_path or os.getenv("CLAUDE_CODE_PROJECT_PATH", str(Path.cwd()))
        self.test_paths = test_paths or ["tests/"]
        self.timeout_seconds = timeout_seconds
        self.strict_types = strict_types

    @property
    def supported_types(self) -> List[str]:
        return ["test"]

    def supports_streaming(self) -> bool:
        return True

    async def _discover_skills(self) -> List[Skill]:
        return [
            Skill(
                name="run_tests",
                description="Run pytest test suite",
                category="testing",
                parameters={
                    "path": "str - path to test file or directory (optional)",
                    "markers": "str - pytest markers to filter (optional)",
                    "verbose": "bool - verbose output (optional)",
                },
            ),
            Skill(
                name="run_with_coverage",
                description="Run tests with coverage report",
                category="testing",
            ),
            Skill(
                name="run_specific",
                description="Run a specific test by name",
                category="testing",
                parameters={
                    "test_name": "str - full test name (e.g., tests/test_foo.py::test_bar)",
                },
            ),
        ]

    async def test_connection(self) -> bool:
        """Test if pytest is available."""
        try:
            result = await asyncio.create_subprocess_exec(
                "pytest", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(result.wait(), timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    async def execute(self, task: Task) -> TaskResult:
        """Execute pytest on the specified path.

        Payload:
            path: str - test file or directory (optional, uses config default)
            args: list[str] - additional pytest arguments (optional)
            context: dict - additional context
        """
        import time
        start = time.time()

        test_path = task.payload.get("path", self.test_paths[0] if self.test_paths else "tests/")
        extra_args = task.payload.get("args", [])
        context = task.payload.get("context", {})

        cmd = ["pytest", test_path, "-v", "--tb=short"]
        cmd.extend(extra_args)

        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
                cwd=self.project_path,
            )

            stdout, _ = await asyncio.wait_for(
                result.communicate(),
                timeout=self.timeout_seconds,
            )

            output = stdout.decode("utf-8", errors="replace")
            duration = time.time() - start

            # Parse output for pass/fail
            passed, failed, errors = self._parse_pytest_output(output)
            all_passed = failed == 0 and errors == 0 and result.returncode == 0

            return TaskResult(
                success=all_passed,
                output=output,
                metadata={
                    "return_code": result.returncode,
                    "passed": passed,
                    "failed": failed,
                    "errors": errors,
                    "test_path": test_path,
                },
                duration_seconds=duration,
            )

        except asyncio.TimeoutError:
            return TaskResult(
                success=False,
                error=f"Timeout after {self.timeout_seconds}s",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return TaskResult(
                success=False,
                error=str(e),
                duration_seconds=time.time() - start,
            )

    async def execute_streaming(
        self,
        task: Task,
        callback: Callable[[str], Coroutine[Any, Any, None]],
    ) -> TaskResult:
        """Execute pytest with streaming output."""
        import time
        import os
        import pty
        start = time.time()

        test_path = task.payload.get("path", self.test_paths[0] if self.test_paths else "tests/")
        extra_args = task.payload.get("args", [])
        context = task.payload.get("context", {})

        cmd = ["pytest", test_path, "-v", "--tb=short", "-s"]
        cmd.extend(extra_args)

        master_fd, slave_fd = os.openpty()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=slave_fd,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.project_path,
            )

            os.close(slave_fd)

            output_chunks = []
            loop = asyncio.get_event_loop()

            while True:
                try:
                    chunk = await asyncio.wait_for(
                        loop.run_in_executor(None, os.read, master_fd, 1024),
                        timeout=0.5,
                    )
                    if not chunk:
                        break

                    text = chunk.decode("utf-8", errors="replace")
                    output_chunks.append(text)

                    if callback:
                        await callback(text)

                    if process.poll() is not None:
                        break

                except asyncio.TimeoutError:
                    if process.poll() is not None:
                        break

            os.close(master_fd)

            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

            duration = time.time() - start
            full_output = "".join(output_chunks)
            passed, failed, errors = self._parse_pytest_output(full_output)
            all_passed = failed == 0 and errors == 0 and process.returncode == 0

            return TaskResult(
                success=all_passed,
                output=full_output,
                metadata={
                    "return_code": process.returncode,
                    "passed": passed,
                    "failed": failed,
                    "errors": errors,
                    "test_path": test_path,
                },
                duration_seconds=duration,
            )

        except Exception as e:
            try:
                os.close(master_fd)
            except Exception:
                pass
            return TaskResult(
                success=False,
                error=str(e),
                duration_seconds=time.time() - start,
            )

    def _parse_pytest_output(self, output: str) -> tuple:
        """Parse pytest output to extract pass/fail counts.

        Returns: (passed, failed, errors)
        """
        passed = 0
        failed = 0
        errors = 0

        # Pattern: "X passed", "X failed", "X error"
        passed_match = re.search(r"(\d+) passed", output)
        if passed_match:
            passed = int(passed_match.group(1))

        failed_match = re.search(r"(\d+) failed", output)
        if failed_match:
            failed = int(failed_match.group(1))

        error_match = re.search(r"(\d+) error", output)
        if error_match:
            errors = int(error_match.group(1))

        # Also check for collection errors
        if "ERROR collecting" in output or "import error" in output.lower():
            errors += 1

        return passed, failed, errors

    async def get_metadata(self) -> Dict[str, Any]:
        base = await super().get_metadata()
        base.update({
            "project_path": self.project_path,
            "test_paths": self.test_paths,
            "timeout_seconds": self.timeout_seconds,
        })
        return base
