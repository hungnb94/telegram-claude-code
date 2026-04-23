#!/usr/bin/env python3
"""
Telegram → Claude Code POC

User sends message via Telegram → Claude Code CLI runs locally → streams progress back to Telegram.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from telegram import Bot, Update
from telegram.error import TelegramError

SCRIPT_DIR = Path(__file__).parent
TASKS_FILE = SCRIPT_DIR / "tasks.json"
KAIZEN_FILE = SCRIPT_DIR / "kaizen_recommendations.json"
CHUNK_SIZE = 30
KAIZEN_SCAN_INTERVAL_HOURS = 6


class KaizenScanner:
    """Lightweight in-process scanner that analyzes patterns and generates ranked recommendations."""

    def __init__(self, project_path: str, tasks_file: Path, kaizen_file: Path):
        self.project_path = Path(project_path)
        self.tasks_file = tasks_file
        self.kaizen_file = kaizen_file
        self._last_scan: Optional[datetime] = None

    def should_rescan(self) -> bool:
        if not self._last_scan:
            return True
        elapsed = datetime.now() - self._last_scan
        return elapsed >= timedelta(hours=KAIZEN_SCAN_INTERVAL_HOURS)

    def scan(self) -> list[dict]:
        """Analyze task history and codebase, return ranked recommendations."""
        recommendations = []

        # Signal 1: Task execution patterns
        task_patterns = self._analyze_task_history()
        recommendations.extend(task_patterns)

        # Signal 2: Codebase quality signals
        code_signals = self._analyze_codebase()
        recommendations.extend(code_signals)

        # Sort by priority (High > Medium > Low)
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda r: (priority_order.get(r.get("priority", "low"), 2), -r.get("impact", 0)))

        self._last_scan = datetime.now()
        self._save(recommendations)
        return recommendations

    def _analyze_task_history(self) -> list[dict]:
        """Analyze tasks.json for patterns - failed tasks, queue growth, common errors."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations

        completed = data.get("completed", [])
        pending = data.get("pending", [])
        failed = [t for t in completed if not t.get("success", True)]

        # Pattern: High failure rate
        if len(completed) >= 5 and len(failed) / len(completed) > 0.3:
            recommendations.append({
                "id": "task_failure_rate",
                "title": "High task failure rate detected",
                "description": f"{len(failed)}/{len(completed)} tasks failed. Investigate root causes.",
                "priority": "high",
                "impact": 80,
                "category": "reliability",
                "action": "Analyze failed tasks for common error patterns"
            })

        # Pattern: Growing queue backlog
        if len(pending) > 3:
            recommendations.append({
                "id": "queue_backlog",
                "title": "Task queue backlog growing",
                "description": f"{len(pending)} tasks waiting. Consider scaling worker or optimizing timeout.",
                "priority": "medium",
                "impact": 60,
                "category": "performance",
                "action": "Review queue processing efficiency"
            })

        # Pattern: Long task durations (inferred from completed tasks)
        if len(completed) >= 3:
            recent = completed[-3:]
            recommendations.append({
                "id": "task_optimization",
                "title": "Optimize task execution patterns",
                "description": f"Analyze recent {len(recent)} completed tasks for optimization opportunities.",
                "priority": "low",
                "impact": 40,
                "category": "performance",
                "action": "Profile task execution to reduce latency"
            })

        return recommendations

    def _analyze_codebase(self) -> list[dict]:
        """Static analysis of codebase for tech debt, complexity, test coverage."""
        recommendations = []
        main_script = SCRIPT_DIR / "telegram_claude_poc.py"

        if not main_script.exists():
            return recommendations

        try:
            with open(main_script) as f:
                lines = f.readlines()

            # Signal: Large file complexity
            if len(lines) > 400:
                recommendations.append({
                    "id": "code_complexity",
                    "title": "Main script exceeds 400 lines",
                    "description": f"File has {len(lines)} lines. Consider splitting into modules.",
                    "priority": "medium",
                    "impact": 50,
                    "category": "maintainability",
                    "action": "Extract classes into separate modules (TaskQueue, ClaudeSubprocess, StreamHandler)"
                })

            # Signal: Missing type hints (heuristic: check first 50 lines)
            has_types = any("->" in line or ": str" in line or ": int" in line or ": dict" in line for line in lines[:50])
            if not has_types:
                recommendations.append({
                    "id": "type_safety",
                    "title": "Missing type annotations",
                    "description": "Add type hints for better maintainability and bug prevention.",
                    "priority": "low",
                    "impact": 35,
                    "category": "code_quality",
                    "action": "Add return type annotations to public methods"
                })

        except Exception:
            pass

        # Check for missing test file
        test_file = SCRIPT_DIR / "tests" / "test_bot.py"
        if not test_file.exists():
            recommendations.append({
                "id": "test_coverage",
                "title": "No tests directory found",
                "description": "Add pytest tests for critical paths (queue, subprocess, handlers).",
                "priority": "medium",
                "impact": 55,
                "category": "code_quality",
                "action": "Create tests/test_bot.py with basic functionality tests"
            })

        return recommendations

    def _save(self, recommendations: list[dict]):
        data = {
            "generated_at": datetime.now().isoformat(),
            "scan_count": 1,
            "recommendations": recommendations
        }
        try:
            with open(self.kaizen_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def get_latest(self) -> Optional[list[dict]]:
        """Load cached recommendations without rescan."""
        try:
            with open(self.kaizen_file) as f:
                data = json.load(f)
            return data.get("recommendations", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return None


class TaskQueue:
    def __init__(self, tasks_file: Path):
        self.tasks_file = tasks_file
        self._ensure_file()

    def _ensure_file(self):
        if not self.tasks_file.exists():
            self._save({"pending": [], "running": None, "completed": []})

    def _load(self) -> dict:
        try:
            with open(self.tasks_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"pending": [], "running": None, "completed": []}

    def _save(self, data: dict):
        with open(self.tasks_file, "w") as f:
            json.dump(data, f, indent=2)

    def enqueue(self, task: dict) -> str:
        data = self._load()
        data["pending"].append(task)
        self._save(data)
        return task["message_id"]

    def peek(self) -> Optional[dict]:
        data = self._load()
        if data["pending"]:
            return data["pending"][0]
        return None

    def dequeue(self) -> Optional[dict]:
        data = self._load()
        if not data["pending"]:
            return None
        task = data["pending"].pop(0)
        data["running"] = task
        self._save(data)
        return task

    def set_running(self, task: dict):
        data = self._load()
        data["running"] = task
        data["pending"] = [t for t in data["pending"] if t["message_id"] != task["message_id"]]
        self._save(data)

    def complete(self, message_id: str, success: bool = True):
        data = self._load()
        if data["running"] and data["running"]["message_id"] == message_id:
            data["completed"].append({**data["running"], "success": success, "completed_at": datetime.now().isoformat()})
            data["running"] = None
            self._save(data)

    def mark_failed(self, message_id: str):
        self.complete(message_id, success=False)

    def has_running_task(self) -> bool:
        data = self._load()
        return data["running"] is not None


class ClaudeSubprocess:
    def __init__(self, project_path: str, timeout_minutes: int = 30):
        self.project_path = project_path
        self.timeout_seconds = timeout_minutes * 60
        self.process: Optional[subprocess.Popen] = None

    def run(self, task_description: str, output_callback, error_callback) -> bool:
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
            output = self.process.stdout.read()
            for line in output.splitlines():
                if line:
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


class StreamHandler:
    def __init__(self, lines_per_chunk: int = CHUNK_SIZE):
        self.lines_per_chunk = lines_per_chunk
        self.buffer: list[str] = []

    def add_line(self, line: str) -> list[str]:
        self.buffer.append(line)
        if len(self.buffer) >= self.lines_per_chunk:
            result = self.buffer
            self.buffer = []
            return result
        return []

    def flush(self) -> list[str]:
        result = self.buffer
        self.buffer = []
        return result


class TelegramClaudeBot:
    def __init__(self, config: dict):
        self.bot = Bot(token=config["telegram_bot_token"])
        self.project_path = config["claude_code_project_path"]
        self.poll_interval = config.get("poll_interval_seconds", 5)
        self.timeout_minutes = config.get("task_timeout_minutes", 30)
        raw_usernames = config.get("allowed_telegram_usernames")
        if raw_usernames is not None and len(raw_usernames) > 0:
            self.allowed_usernames = set(u.lower() for u in raw_usernames)
        else:
            self.allowed_usernames = None
        self.queue = TaskQueue(TASKS_FILE)
        self.kaizen = KaizenScanner(self.project_path, TASKS_FILE, KAIZEN_FILE)
        self.running = True
        self.streaming_message: dict = {}
        self._offset: Optional[int] = None
        self._task_lock = asyncio.Lock()
        self._last_kaizen_check: Optional[datetime] = None
        self._pending_kaizen_selection: Optional[str] = None  # chat_id của user đang chọn kaizen

    async def send_text(self, chat_id: str, text: str, reply_to: Optional[str] = None):
        try:
            await self.bot.send_message(
                text=text,
                chat_id=chat_id,
                reply_to_message_id=reply_to,
            )
        except TelegramError as e:
            print(f"Failed to send message: {e}", file=sys.stderr)

    async def edit_message(self, chat_id: str, message_id: str, text: str):
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=int(message_id),
            )
        except TelegramError:
            pass

    async def handle_start(self, update: Update):
        await update.message.reply_text(
            "Claude Code Telegram Bot\n\n"
            "Send me a task description and I'll execute it using Claude Code on your local codebase.\n\n"
            "Tasks are queued if a task is already running.\n\n"
            "Use /kaizen to see improvement recommendations."
        )

    async def handle_kaizen(self, update: Update):
        """Handle /kaizen command - show ranked recommendations."""
        recommendations = self.kaizen.get_latest()

        # If no cached recommendations or stale, trigger new scan
        if recommendations is None or self.kaizen.should_rescan():
            await update.message.reply_text("🔍 Scanning codebase for improvements...")
            recommendations = self.kaizen.scan()

        if not recommendations:
            await update.message.reply_text("✅ No improvements needed right now. Code looks healthy!")
            return

        # Store recommendations và chat_id để handle reply
        self._pending_kaizen_selection = str(update.message.chat_id)
        self._cached_recommendations = recommendations

        report = self.format_kaizen_report(recommendations)
        await update.message.reply_text(report)

    @staticmethod
    def format_kaizen_report(recommendations: list[dict]) -> str:
        """Format recommendations as ranked numbered list."""
        lines = ["📊 *Kaizen Recommendations*\n"]
        for i, rec in enumerate(recommendations, 1):
            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rec.get("priority", "low"), "⚪")
            lines.append(f"{i}. {priority_emoji} *{rec['title']}*")
            lines.append(f"   {rec['description']}")
            lines.append(f"   Action: {rec.get('action', 'Review and implement')}\n")
        lines.append("\nReply with a number to prioritize this task.")
        return "\n".join(lines)

    async def handle_message(self, update: Update):
        if update.message and update.message.text:
            text = update.message.text.strip()
            chat_id = str(update.message.chat_id)
            message_id = str(update.message.message_id)

            # Authorization check
            if self.allowed_usernames is not None:
                username = (update.message.from_user.username or "").lower()
                if username not in self.allowed_usernames:
                    await update.message.reply_text("⛔ You are not authorized to use this bot.")
                    return

            # Check if user is responding to kaizen selection (within 2 minutes)
            if (self._pending_kaizen_selection == chat_id and
                hasattr(self, '_cached_recommendations') and
                self._cached_recommendations):
                try:
                    choice_idx = int(text) - 1
                    if 0 <= choice_idx < len(self._cached_recommendations):
                        selected = self._cached_recommendations[choice_idx]
                        action = selected.get('action', '')
                        # Clear pending state
                        self._pending_kaizen_selection = None
                        self._cached_recommendations = None
                        # Execute the kaizen task via Claude Code
                        self.queue.set_running({"message_id": message_id, "text": action, "chat_id": chat_id})
                        await update.message.reply_text(f"🎯 Executing: {selected['title']}\n\n{action}")
                        asyncio.create_task(self._execute_task(message_id, chat_id, action))
                        return
                except (ValueError, IndexError):
                    # Not a valid number, fall through to normal task handling
                    pass

            if self.queue.has_running_task():
                self.queue.enqueue({"message_id": message_id, "text": text, "chat_id": chat_id})
                await update.message.reply_text(f"Task queued (position: {len(self.queue.peek())})")
            else:
                self.queue.set_running({"message_id": message_id, "text": text, "chat_id": chat_id})
                await update.message.reply_text("Starting task...")
                asyncio.create_task(self._execute_task(message_id, chat_id, text, is_kaizen=(self._pending_kaizen_selection is not None and self._pending_kaizen_selection == chat_id)))

    async def _execute_task(self, message_id: str, chat_id: str, text: str, is_kaizen: bool = False):
        async with self._task_lock:
            stream_handler = StreamHandler()

            # For kaizen tasks, run Claude Code on the bot's own directory to improve itself
            run_path = SCRIPT_DIR if is_kaizen else self.project_path
        async with self._task_lock:
            stream_handler = StreamHandler()

            try:
                streaming_msg = await self.bot.send_message(
                    text="⏳ Running...",
                    chat_id=chat_id,
                    reply_to_message_id=int(message_id),
                )
                self.streaming_message[message_id] = str(streaming_msg.message_id)
            except TelegramError as e:
                print(f"Failed to create streaming message: {e}", file=sys.stderr)
                self.queue.complete(message_id, False)
                return

            def output_callback(line: str):
                chunks = stream_handler.add_line(line)
                for chunk in chunks:
                    asyncio.create_task(self.edit_message(chat_id, self.streaming_message.get(message_id, ""), "\n".join(stream_handler.buffer)))

            def error_callback(line: str):
                asyncio.create_task(self.send_text(chat_id, f"❌ {line}"))

            claude = ClaudeSubprocess(run_path, self.timeout_minutes)
            success = claude.run(text, output_callback, error_callback)

            remaining = stream_handler.flush()
            if remaining:
                asyncio.create_task(self.edit_message(chat_id, self.streaming_message.get(message_id, ""), "\n".join(remaining)))

            self.queue.complete(message_id, success)

            final_text = "✅ Task completed" if success else "❌ Task failed"
            asyncio.create_task(self.edit_message(chat_id, self.streaming_message.get(message_id, ""), final_text))
            self.streaming_message.pop(message_id, None)

    async def poll_loop(self):
        while self.running:
            try:
                # Periodic kaizen check (every poll_interval, not every 6 hours in this poll)
                # Actual 6-hour interval is enforced in _periodic_kaizen_check
                if self._last_kaizen_check is None:
                    self._last_kaizen_check = datetime.now()

                updates = await self.bot.get_updates(timeout=self.poll_interval, offset=self._offset)
                if updates:
                    for update in updates:
                        if update.message:
                            if update.message.text == "/start":
                                await self.handle_start(update)
                            elif update.message.text == "/kaizen":
                                await self.handle_kaizen(update)
                            elif update.message.text.startswith("/"):
                                pass
                            else:
                                await self.handle_message(update)
                        self._offset = update.update_id + 1
                await asyncio.sleep(self.poll_interval)
            except TelegramError as e:
                print(f"Poll error: {e}", file=sys.stderr)
                await asyncio.sleep(10)
            except Exception as e:
                print(f"Unexpected error: {e}", file=sys.stderr)
                await asyncio.sleep(5)

    async def _setup_signal_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_event_loop().add_signal_handler(sig, self._shutdown)

    def _shutdown(self):
        self.running = False

    def start(self):
        asyncio.run(self._async_main())

    async def _async_main(self):
        await self._setup_signal_handlers()
        asyncio.create_task(self._periodic_kaizen_check())
        await self.poll_loop()

    async def _periodic_kaizen_check(self):
        """Run kaizen scan every 6 hours and notify if new recommendations found."""
        while self.running:
            await asyncio.sleep(KAIZEN_SCAN_INTERVAL_HOURS * 3600)

            if not self.kaizen.should_rescan():
                continue

            recommendations = self.kaizen.scan()
            if recommendations and self.allowed_usernames:
                # Notify all allowed users about new recommendations
                for username in self.allowed_usernames:
                    try:
                        # Get chat_id from updates or stored - for now just log
                        print(f"📊 Kaizen scan complete: {len(recommendations)} recommendations generated")
                    except Exception as e:
                        print(f"Failed to notify kaizen: {e}", file=sys.stderr)


def load_config() -> dict:
    config_file = SCRIPT_DIR / "config.yaml"
    if config_file.exists():
        with open(config_file) as f:
            config = yaml.safe_load(f)
    else:
        config = {}
    config["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", config.get("telegram_bot_token", ""))
    config["claude_code_project_path"] = os.environ.get("CLAUDE_CODE_PROJECT_PATH", config.get("claude_code_project_path", ""))
    config["poll_interval_seconds"] = int(os.environ.get("POLL_INTERVAL_SECONDS", config.get("poll_interval_seconds", 5)))
    config["task_timeout_minutes"] = int(os.environ.get("TASK_TIMEOUT_MINUTES", config.get("task_timeout_minutes", 30)))
    raw_usernames = os.environ.get("ALLOWED_TELEGRAM_USERNAMES", config.get("allowed_telegram_usernames", ""))
    if isinstance(raw_usernames, list):
        config["allowed_telegram_usernames"] = [u.strip() for u in raw_usernames if u.strip()]
    else:
        config["allowed_telegram_usernames"] = [u.strip() for u in raw_usernames.split(",") if u.strip()]

    if not config["telegram_bot_token"]:
        print("TELEGRAM_BOT_TOKEN environment variable or config.yaml required", file=sys.stderr)
        sys.exit(1)
    if not config["claude_code_project_path"]:
        print("CLAUDE_CODE_PROJECT_PATH environment variable or config.yaml required", file=sys.stderr)
        sys.exit(1)

    return config


def main():
    config = load_config()
    bot = TelegramClaudeBot(config)
    print("Bot started. Press Ctrl+C to stop.")
    bot.start()


if __name__ == "__main__":
    main()
