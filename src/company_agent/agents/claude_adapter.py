"""Claude agent adapter - executes code tasks using Claude CLI.

Claude is used for main coding tasks - implementing features, writing code,
and other development work.

Supports streaming output via PTY for real-time feedback.
"""

import asyncio
import os
import re
import pty
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from company_agent.agents.base import AgentAdapter, Skill, TaskResult
from company_agent.task_queue import Task
from company_agent.workflow.clarification import ClarificationRequested, ClarificationType


class ClaudeAdapter(AgentAdapter):
    """Adapter for Claude CLI.

    Claude is used for main coding tasks - implementing features, writing code,
    refactoring, etc.

    Environment:
        CLAUDE_CODE_PROJECT_PATH - path to the project directory
        CLAUDE_MODEL - model to use (optional)
    """

    def __init__(
        self,
        project_path: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 300,
        max_retries: int = 2,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        super().__init__(name="claude")
        self.project_path = project_path or os.getenv("CLAUDE_CODE_PROJECT_PATH", str(Path.cwd()))
        self.model = model or os.getenv("CLAUDE_MODEL")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def supported_types(self) -> List[str]:
        return ["code", "refactor", "plan"]

    def supports_streaming(self) -> bool:
        return True

    async def _discover_skills(self) -> List[Skill]:
        return [
            Skill(
                name="implement",
                description="Implement a feature or component",
                category="code",
                parameters={
                    "prompt": "str - detailed description of what to implement",
                },
            ),
            Skill(
                name="refactor",
                description="Refactor existing code",
                category="code",
                parameters={
                    "prompt": "str - what to refactor and how",
                    "files": "list[str] - files to refactor",
                },
            ),
            Skill(
                name="write_tests",
                description="Write tests for existing code",
                category="testing",
                parameters={
                    "files": "list[str] - files to test",
                },
            ),
        ]

    async def test_connection(self) -> bool:
        """Test if Claude CLI is available."""
        try:
            result = await asyncio.create_subprocess_exec(
                "claude", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(result.wait(), timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _detect_clarification(self, prompt: str, context: Dict[str, Any]) -> None:
        """Detect ambiguous scenarios that need user clarification.

        Raises ClarificationRequested if the prompt has ambiguity indicators.
        The context (including chat_id) is passed through to the exception.
        """
        prompt_lower = prompt.lower()

        # Ambiguous patterns
        ambiguous_patterns = {
            "which_database": {
                "keywords": ["which database", "what database", "database or", "postgresql or", "sqlite or"],
                "question": "Which database should I use?",
                "options": ["PostgreSQL", "SQLite", "MongoDB", "Let me decide later"],
            },
            "which_framework": {
                "keywords": ["which framework", "what framework", "framework or", "react or", "vue or"],
                "question": "Which framework do you prefer?",
                "options": ["React", "Vue.js", "Angular", "Let me decide later"],
            },
            "api_style": {
                "keywords": ["rest api", "graphql api", "api design", "rest or graphql"],
                "question": "Which API style should I use?",
                "options": ["REST", "GraphQL", "gRPC", "Let me decide later"],
            },
            "architecture": {
                "keywords": ["microservices", "monolith", "serverless", "monolithic or"],
                "question": "Which architecture style?",
                "options": ["Monolith", "Microservices", "Serverless", "Let me decide later"],
            },
            "auth_strategy": {
                "keywords": ["jwt or", "session or", "oauth or", "auth strategy", "authentication approach"],
                "question": "Which authentication strategy?",
                "options": ["JWT", "Session-based", "OAuth 2.0", "Let me decide later"],
            },
        }

        for pattern_id, pattern in ambiguous_patterns.items():
            if any(kw in prompt_lower for kw in pattern["keywords"]):
                raise ClarificationRequested(
                    question=pattern["question"],
                    options=pattern["options"],
                    clarification_type=ClarificationType.CHOICE,
                    context=context,  # Pass through chat_id and other context
                )

    async def execute(self, task: Task) -> TaskResult:
        """Execute a code task using Claude CLI.

        Payload:
            prompt: str - the instruction for Claude
            files: list[str] - files to work with (optional)
            context: dict - additional context (includes chat_id for clarification)
        """
        import time
        start = time.time()

        prompt = task.payload.get("prompt", "")
        files = task.payload.get("files", [])
        context = task.payload.get("context", {})

        if not prompt:
            return TaskResult(success=False, error="No prompt provided")

        # Check for ambiguous scenarios that need user input
        self._detect_clarification(prompt, context)

        cmd = self._build_command(prompt, files)

        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_path,
            )

            stdout, stderr = await asyncio.wait_for(
                result.communicate(),
                timeout=self.timeout_seconds,
            )

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")

            duration = time.time() - start

            # Claude CLI returns non-zero for various reasons
            # Check for actual errors in output
            has_error = any(indicator in output.lower() for indicator in ["error:", "exception", "traceback"])
            success = result.returncode == 0 and not has_error

            return TaskResult(
                success=success,
                output=output,
                error=error if error and "error" in error.lower() else None,
                metadata={
                    "return_code": result.returncode,
                    "prompt": prompt[:100],  # Truncate for storage
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
        """Execute Claude with streaming output via PTY.

        This provides real-time output as Claude is running,
        which can be streamed to Telegram for live feedback.
        """
        import time
        import os
        start = time.time()

        prompt = task.payload.get("prompt", "")
        files = task.payload.get("files", [])
        context = task.payload.get("context", {})

        if not prompt:
            return TaskResult(success=False, error="No prompt provided")

        # Check for ambiguous scenarios that need user input
        self._detect_clarification(prompt, context)

        cmd = self._build_command(prompt, files)

        master_fd, slave_fd = os.openpty()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=slave_fd,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.project_path,
                # Pass through to handle interactive prompts
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

                    if process.returncode is not None:
                        break

                except asyncio.TimeoutError:
                    # Check if process exited
                    if process.returncode is not None:
                        break

            os.close(master_fd)

            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

            duration = time.time() - start
            full_output = "".join(output_chunks)

            has_error = any(indicator in full_output.lower() for indicator in ["error:", "exception", "traceback"])
            success = process.returncode == 0 and not has_error

            return TaskResult(
                success=success,
                output=full_output,
                metadata={
                    "return_code": process.returncode,
                    "prompt": prompt[:100],
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
        """Build the Claude CLI command.

        Uses --print for non-interactive output with --dangerously-skip-permissions
        for CI/automated environments.
        """
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
        ]

        if self.model:
            cmd.extend(["--model", self.model])

        # Add prompt as argument
        cmd.append(prompt)

        # Add file arguments if provided
        if files:
            cmd.extend(["--", *files])

        return cmd

    def _build_env(self) -> Dict[str, str]:
        """Build environment variables."""
        env = os.environ.copy()
        if self.project_path:
            env["CLAUDE_CODE_PROJECT_PATH"] = self.project_path
        if self.model:
            env["CLAUDE_MODEL"] = self.model
        return env

    async def get_metadata(self) -> Dict[str, Any]:
        base = await super().get_metadata()
        base.update({
            "project_path": self.project_path,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
        })
        return base
