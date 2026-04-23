import os
import subprocess
from typing import Callable, Optional


class ClaudeSubprocess:
    def __init__(self, project_path: str, timeout_minutes: int = 30):
        self.project_path = project_path
        self.timeout_seconds = timeout_minutes * 60
        self.process: Optional[subprocess.Popen] = None

    def run(self, task_description: str, output_callback: Callable[[str], None], error_callback: Callable[[str], None]) -> bool:
        self.process = subprocess.Popen(
            ["claude", "--print", "--dangerously-skip-permissions", "--no-session-persistence"],
            cwd=self.project_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        try:
            self.process.stdin.write(f"Your task: {task_description}\n")
            self.process.stdin.flush()
            self.process.stdin.close()

            # Stream line-by-line instead of buffering all output
            for line in self.process.stdout:
                if line := line.rstrip("\n"):
                    output_callback(line)

            self.process.wait(timeout=self.timeout_seconds)
            return self.process.returncode == 0
        except subprocess.TimeoutExpired:
            error_callback("Task timed out")
            self.kill()
            return False
        except Exception as e:
            error_callback(f"Error: {e}")
            self.kill()
            return False
        finally:
            self.process = None

    def kill(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()