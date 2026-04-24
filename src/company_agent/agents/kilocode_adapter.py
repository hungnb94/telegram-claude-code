"""KiloCode agent adapter - executes code review using KiloCode CLI.

KiloCode is used for code review tasks - analyzing code quality,
finding issues, and providing feedback.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from company_agent.agents.base import AgentAdapter, Skill, TaskResult
from company_agent.task_queue import Task


class KiloCodeAdapter(AgentAdapter):
    """Adapter for KiloCode CLI.

    KiloCode is designed for code review and analysis.
    It can review code changes, provide feedback, and suggest improvements.

    Environment:
        KILOCODE_PROJECT_PATH - path to the project directory
    """

    def __init__(
        self,
        project_path: Optional[str] = None,
        timeout_seconds: int = 300,
        max_retries: int = 2,
        pass_keywords: Optional[List[str]] = None,
        fail_keywords: Optional[List[str]] = None,
    ):
        super().__init__(name="kilocode")
        self.project_path = project_path or os.getenv("KILOCODE_PROJECT_PATH") or os.getenv("CLAUDE_CODE_PROJECT_PATH", str(Path.cwd()))
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.pass_keywords = pass_keywords or ["approved", "lgtm", "looks good", "no issues", "pass", "looks great", "good job"]
        self.fail_keywords = fail_keywords or ["issue", "problem", "warning", "error", "bug", "security", "should fix", "needs work"]

    @property
    def supported_types(self) -> List[str]:
        return ["review"]

    def supports_streaming(self) -> bool:
        return True

    async def _discover_skills(self) -> List[Skill]:
        return [
            Skill(
                name="review_code",
                description="Review code for issues, bugs, and improvements",
                category="review",
            ),
            Skill(
                name="review_pr",
                description="Review a pull request",
                category="review",
                parameters={
                    "pr_url": "str - URL of the pull request",
                },
            ),
            Skill(
                name="analyze_complexity",
                description="Analyze code complexity and maintainability",
                category="analysis",
            ),
        ]

    async def test_connection(self) -> bool:
        """Test if KiloCode CLI is available."""
        try:
            result = await asyncio.create_subprocess_exec(
                "kilocode", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(result.wait(), timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    async def execute(self, task: Task) -> TaskResult:
        """Execute a review task using KiloCode.

        Payload:
            prompt: str - review instructions
            files: list[str] - files to review (optional)
            context: dict - additional context
        """
        import time
        start = time.time()

        prompt = task.payload.get("prompt", "")
        files = task.payload.get("files", [])
        context = task.payload.get("context", {})

        if not prompt:
            return TaskResult(success=False, error="No review prompt provided")

        # Build the command
        cmd = self._build_command(prompt, files)

        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_path,
                env=self._build_env(),
            )

            stdout, stderr = await asyncio.wait_for(
                result.communicate(),
                timeout=self.timeout_seconds,
            )

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")

            duration = time.time() - start

            # Determine pass/fail based on keywords
            is_approved = self._evaluate_review(output)

            if result.returncode == 0 or is_approved:
                return TaskResult(
                    success=True,
                    output=output,
                    metadata={
                        "return_code": result.returncode,
                        "approved": is_approved,
                        "score": self._calculate_score(output),
                    },
                    duration_seconds=duration,
                )
            else:
                return TaskResult(
                    success=False,
                    output=output,
                    error=error or "Review failed",
                    metadata={
                        "return_code": result.returncode,
                        "approved": is_approved,
                        "score": self._calculate_score(output),
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
        """Execute review with streaming output."""
        import time
        import os
        import pty
        start = time.time()

        prompt = task.payload.get("prompt", "")
        files = task.payload.get("files", [])
        context = task.payload.get("context", {})

        if not prompt:
            return TaskResult(success=False, error="No review prompt provided")

        cmd = self._build_command(prompt, files)

        master_fd, slave_fd = os.openpty()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.project_path,
                env=self._build_env(),
            )

            os.close(slave_fd)

            output_chunks = []
            loop = asyncio.get_event_loop()

            while True:
                try:
                    chunk = await asyncio.wait_for(
                        loop.run_in_executor(None, os.read, master_fd, 4096),
                        timeout=0.1,
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
            is_approved = self._evaluate_review(full_output)

            return TaskResult(
                success=process.returncode == 0 or is_approved,
                output=full_output,
                metadata={
                    "return_code": process.returncode,
                    "approved": is_approved,
                    "score": self._calculate_score(full_output),
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

    def _build_command(self, prompt: str, files: List[str]) -> List[str]:
        """Build the KiloCode CLI command."""
        cmd = ["kilocode", "review"]

        if files:
            cmd.extend(files)
        else:
            cmd.append("--all")

        # Add prompt as the review instruction
        cmd.extend(["--prompt", prompt])

        return cmd

    def _build_env(self) -> Dict[str, str]:
        """Build environment variables."""
        env = os.environ.copy()
        if self.project_path:
            env["KILOCODE_PROJECT_PATH"] = self.project_path
        return env

    def _evaluate_review(self, output: str) -> bool:
        """Evaluate if the review passed based on keywords."""
        output_lower = output.lower()

        # Check for fail keywords first
        for keyword in self.fail_keywords:
            if keyword.lower() in output_lower:
                return False

        # Check for pass keywords
        for keyword in self.pass_keywords:
            if keyword.lower() in output_lower:
                return True

        return False

    def _calculate_score(self, output: str) -> float:
        """Calculate a review score (0-10) based on the output.

        This is a heuristic based on keyword presence.
        """
        score = 5.0  # Default neutral score
        output_lower = output.lower()

        # Positive indicators
        positive = sum(1 for kw in ["good", "great", "excellent", "clean", "well"] if kw in output_lower)
        score += positive * 0.5

        # Negative indicators
        negative = sum(1 for kw in ["issue", "problem", "bug", "error", "warning", "should"] if kw in output_lower)
        score -= negative * 0.5

        # Clamp to 0-10
        return max(0.0, min(10.0, score))

    async def get_metadata(self) -> Dict[str, Any]:
        base = await super().get_metadata()
        base.update({
            "project_path": self.project_path,
            "timeout_seconds": self.timeout_seconds,
            "pass_keywords": self.pass_keywords,
        })
        return base
