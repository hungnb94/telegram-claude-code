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


SIGNAL_REGISTRY: dict[str, callable] = {}


def register_signal(signal_id: str):
    """Decorator to register analyzer methods."""
    def decorator(method):
        SIGNAL_REGISTRY[signal_id] = method
        return method
    return decorator


class KaizenConfig:
    """Default kaizen configuration."""
    DEFAULT_CATEGORIES = {
        "reliability": {
            "min_results": 2,
            "signals": ["high_failure_rate", "task_timeout", "queue_stall"],
        },
        "performance": {
            "min_results": 2,
            "signals": ["queue_backlog", "slow_tasks"],
        },
        "maintainability": {
            "min_results": 2,
            "signals": ["code_complexity", "large_file"],
        },
        "code_quality": {
            "min_results": 2,
            "signals": ["missing_tests", "missing_types"],
        },
        "feature_requests": {
            "min_results": 2,
            "signals": ["requested_feature", "repeated_pattern"],
        },
    }

    @classmethod
    def from_dict(cls, config: dict) -> "KaizenConfig":
        """Merge user config with defaults."""
        instance = cls()
        instance.categories = {**cls.DEFAULT_CATEGORIES, **config.get("categories", {})}
        instance.scan_interval_hours = config.get("scan_interval_hours", KAIZEN_SCAN_INTERVAL_HOURS)
        return instance


class KaizenScanner:
    """Lightweight in-process scanner that analyzes patterns and generates ranked recommendations."""

    def __init__(self, project_path: str, tasks_file: Path, kaizen_file: Path, config: Optional[dict] = None):
        self.project_path = Path(project_path)
        self.tasks_file = tasks_file
        self.kaizen_file = kaizen_file
        self._last_scan: Optional[datetime] = None
        self.config = KaizenConfig.from_dict(config or {})

    def should_rescan(self) -> bool:
        if not self._last_scan:
            return True
        elapsed = datetime.now() - self._last_scan
        return elapsed >= timedelta(hours=self.config.scan_interval_hours)

    def scan(self) -> list[dict]:
        """Analyze task history and codebase, return ranked recommendations."""
        recommendations = []
        for signal_id, method in SIGNAL_REGISTRY.items():
            results = method(self)
            for r in results:
                r["id"] = signal_id
            recommendations.extend(results)

        # Group by category, apply min_results per category
        by_category: dict[str, list[dict]] = {}
        for rec in recommendations:
            cat = rec.get("category", "uncategorized")
            by_category.setdefault(cat, []).append(rec)

        capped: list[dict] = []
        for cat, cat_recs in by_category.items():
            min_results = self.config.categories.get(cat, {}).get("min_results", 2)
            capped.extend(cat_recs[:min_results])

        # Sort by priority (High > Medium > Low)
        priority_order = {"high": 0, "medium": 1, "low": 2}
        capped.sort(key=lambda r: (priority_order.get(r.get("priority", "low"), 2), -r.get("impact", 0)))

        self._last_scan = datetime.now()
        self._save(capped)
        return capped

    @register_signal("high_failure_rate")
    def _analyze_high_failure_rate(self) -> list[dict]:
        """Task failure rate ≥30% (of 5+ completed) → reliability."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations
        completed = data.get("completed", [])
        failed = [t for t in completed if not t.get("success", True)]
        if len(completed) >= 5 and len(failed) / len(completed) > 0.3:
            recommendations.append({
                "title": "High task failure rate detected",
                "description": f"{len(failed)}/{len(completed)} tasks failed. Investigate root causes.",
                "priority": "high",
                "impact": 80,
                "category": "reliability",
                "action": "Analyze failed tasks for common error patterns"
            })
        return recommendations

    @register_signal("task_timeout")
    def _analyze_task_timeout(self) -> list[dict]:
        """Tasks that timed out → reliability."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations
        completed = data.get("completed", [])
        timed_out = [t for t in completed if t.get("success") is False and "timeout" in t.get("text", "").lower()]
        if timed_out:
            recommendations.append({
                "title": "Task timeout pattern detected",
                "description": f"{len(timed_out)} tasks timed out. Review timeout settings.",
                "priority": "high",
                "impact": 70,
                "category": "reliability",
                "action": "Review task_timeout_minutes config"
            })
        return recommendations

    @register_signal("queue_stall")
    def _analyze_queue_stall(self) -> list[dict]:
        """Running task stalled (stale running timestamp) → reliability."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations
        running = data.get("running")
        if running and "started_at" in running:
            started = datetime.fromisoformat(running["started_at"])
            if datetime.now() - started > timedelta(minutes=self.config.scan_interval_hours * 60):
                recommendations.append({
                    "title": "Task queue stalled",
                    "description": "Running task has not completed in a long time.",
                    "priority": "high",
                    "impact": 85,
                    "category": "reliability",
                    "action": "Investigate the running task or increase timeout"
                })
        return recommendations

    @register_signal("queue_backlog")
    def _analyze_queue_backlog(self) -> list[dict]:
        """Pending queue >3 → performance."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations
        pending = data.get("pending", [])
        if len(pending) > 3:
            recommendations.append({
                "title": "Task queue backlog growing",
                "description": f"{len(pending)} tasks waiting. Consider scaling worker or optimizing timeout.",
                "priority": "medium",
                "impact": 60,
                "category": "performance",
                "action": "Review queue processing efficiency"
            })
        return recommendations

    @register_signal("slow_tasks")
    def _analyze_slow_tasks(self) -> list[dict]:
        """Long-running tasks (recent avg duration) → performance."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations
        completed = data.get("completed", [])
        if len(completed) >= 3:
            recommendations.append({
                "title": "Optimize task execution patterns",
                "description": f"Analyze recent {len(completed[-3:])} completed tasks for optimization opportunities.",
                "priority": "low",
                "impact": 40,
                "category": "performance",
                "action": "Profile task execution to reduce latency"
            })
        return recommendations

    @register_signal("code_complexity")
    def _analyze_code_complexity(self) -> list[dict]:
        """File >400 lines → maintainability."""
        recommendations = []
        main_script = SCRIPT_DIR / "telegram_claude_poc.py"
        if main_script.exists():
            try:
                lines = len(main_script.read_text().splitlines())
                if lines > 400:
                    recommendations.append({
                        "title": "Main script exceeds 400 lines",
                        "description": f"File has {lines} lines. Consider splitting into modules.",
                        "priority": "medium",
                        "impact": 50,
                        "category": "maintainability",
                        "action": "Extract classes into separate modules (TaskQueue, ClaudeSubprocess, StreamHandler)"
                    })
            except Exception:
                pass
        return recommendations

    @register_signal("large_file")
    def _analyze_large_file(self) -> list[dict]:
        """Any file >500 lines → maintainability."""
        recommendations = []
        for py_file in SCRIPT_DIR.glob("*.py"):
            if py_file.name.startswith("."):
                continue
            try:
                lines = len(py_file.read_text().splitlines())
                if lines > 500:
                    recommendations.append({
                        "title": f"Large file: {py_file.name}",
                        "description": f"{py_file.name} has {lines} lines. Consider splitting.",
                        "priority": "medium",
                        "impact": 45,
                        "category": "maintainability",
                        "action": f"Split {py_file.name} into smaller modules"
                    })
            except Exception:
                pass
        return recommendations

    @register_signal("missing_tests")
    def _analyze_missing_tests(self) -> list[dict]:
        """No tests/ directory → code_quality."""
        recommendations = []
        test_file = SCRIPT_DIR / "tests" / "test_bot.py"
        if not test_file.exists():
            recommendations.append({
                "title": "No tests directory found",
                "description": "Add pytest tests for critical paths (queue, subprocess, handlers).",
                "priority": "medium",
                "impact": 55,
                "category": "code_quality",
                "action": "Create tests/test_bot.py with basic functionality tests"
            })
        return recommendations

    @register_signal("missing_types")
    def _analyze_missing_types(self) -> list[dict]:
        """No type hints in first 50 lines → code_quality."""
        recommendations = []
        main_script = SCRIPT_DIR / "telegram_claude_poc.py"
        if main_script.exists():
            try:
                lines = main_script.read_text().splitlines()[:50]
                has_types = any("->" in line or ": str" in line or ": int" in line or ": dict" in line for line in lines)
                if not has_types:
                    recommendations.append({
                        "title": "Missing type annotations",
                        "description": "Add type hints for better maintainability and bug prevention.",
                        "priority": "low",
                        "impact": 35,
                        "category": "code_quality",
                        "action": "Add return type annotations to public methods"
                    })
            except Exception:
                pass
        return recommendations

    @register_signal("requested_feature")
    def _analyze_requested_feature(self) -> list[dict]:
        """Detect feature requests from user task patterns."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations

        task_texts = [t.get("text", "").lower() for t in data.get("completed", [])]

        # Keywords suggesting new features
        feature_keywords = ["add ", "implement", "create ", "new ", "build ", "feature", "would be nice", "wish", "want", "should have"]
        feature_requests = [t for t in task_texts if any(kw in t for kw in feature_keywords)]

        if len(feature_requests) >= 2:
            top_request = feature_requests[-1]
            recommendations.append({
                "title": "User-requested feature detected",
                "description": f'"{top_request[:80]}..." has been requested. Consider adding this.',
                "priority": "medium",
                "impact": 65,
                "category": "feature_requests",
                "action": top_request
            })
        return recommendations

    @register_signal("repeated_pattern")
    def _analyze_repeated_pattern(self) -> list[dict]:
        """Detect repeated user request patterns → deduplication opportunity."""
        recommendations = []
        try:
            with open(self.tasks_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return recommendations

        task_texts = [t.get("text", "").lower() for t in data.get("completed", [])]

        # Find tasks that appear multiple times (normalized)
        from collections import Counter
        normalized = []
        for t in task_texts:
            words = set(t.split())
            if len(words) >= 3:
                normalized.append(" ".join(sorted(words)))

        repeats = Counter(normalized).most_common(1)
        if repeats and repeats[0][1] >= 2:
            original = repeats[0][0]
            recommendations.append({
                "title": "Repeated task pattern detected",
                "description": f'"{original[:60]}" appears {repeats[0][1]} times. Automate this.',
                "priority": "medium",
                "impact": 60,
                "category": "feature_requests",
                "action": f"Create automated routine for: {original[:60]}"
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
        self.kaizen = KaizenScanner(self.project_path, TASKS_FILE, KAIZEN_FILE, config.get("kaizen"))
        self.running = True
        self.streaming_message: dict = {}
        self._offset: Optional[int] = None
        self._task_lock = asyncio.Lock()
        self._last_kaizen_check: Optional[datetime] = None
        self._pending_kaizen_selection: Optional[str] = None  # chat_id của user đang chọn kaizen
        self._cached_recommendations: Optional[list[dict]] = None

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
            run_path = SCRIPT_DIR if is_kaizen else self.project_path

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
                print(f"📊 Kaizen scan complete: {len(recommendations)} recommendations generated")
                print("Run /kaizen to see them.")


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
