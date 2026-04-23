#!/usr/bin/env python3
"""
Telegram → Claude Code POC

User sends message via Telegram → Claude Code CLI runs locally → streams progress back to Telegram.
"""

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from telegram import Bot, BotCommand, Update
from telegram.error import TelegramError

from task_queue import TaskQueue
from claude_subprocess import ClaudeSubprocess
from stream_handler import StreamHandler

SCRIPT_DIR = Path(__file__).parent
TASKS_FILE = SCRIPT_DIR / "tasks.json"
KAIZEN_FILE = SCRIPT_DIR / "kaizen_recommendations.json"
CHUNK_SIZE = 30
KAIZEN_SCAN_INTERVAL_HOURS = 6
KILOCODE_REVIEW_ENABLED = True
KILOCODE_REVIEW_TIMEOUT_SECONDS = 300
REVIEW_MAX_RETRIES_DEFAULT = 3
REVIEW_PASS_KEYWORDS = ["approved", "lgtm", "looks good", "no issues", "pass"]
REVIEW_FAIL_KEYWORDS = ["issue", "problem", "warning", "error", "bug", "security"]
TELEGRAM_MESSAGE_CHUNK_SIZE = 3500


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
        """Merge user config with defaults, deep-merging each category dict."""
        instance = cls()
        instance.categories = {}
        for cat, defaults in cls.DEFAULT_CATEGORIES.items():
            user_cat = config.get("categories", {}).get(cat, {})
            instance.categories[cat] = {**defaults, **user_cat}
        instance.scan_interval_hours = config.get("scan_interval_hours", KAIZEN_SCAN_INTERVAL_HOURS)
        return instance


class KiloCodeReviewer:
    """Cross-review code using KiloCode AI after Claude Code completes a task."""

    def __init__(
        self,
        project_path: str,
        timeout_seconds: int = KILOCODE_REVIEW_TIMEOUT_SECONDS,
        max_retries: int = REVIEW_MAX_RETRIES_DEFAULT,
        pass_keywords: list[str] = REVIEW_PASS_KEYWORDS,
        fail_keywords: list[str] = REVIEW_FAIL_KEYWORDS,
    ):
        self.project_path = project_path
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.pass_keywords = pass_keywords
        self.fail_keywords = fail_keywords

    def review(self, task_description: str) -> tuple[bool, str]:
        """Run KiloCode review on the completed task. Returns (passed, output)."""
        import subprocess

        prompt = (
            f"Review the changes made for this task: '{task_description}'\n\n"
            "Focus on: code quality, potential bugs, security issues, "
            "performance concerns, and adherence to best practices. "
            "Provide specific, actionable feedback. "
            "End your review with 'APPROVED' if no major issues, or list specific issues that need fixing."
        )

        output_lines: list[str] = []

        def collect_output(line: str):
            if line := line.rstrip("\n"):
                output_lines.append(line)

        process = subprocess.Popen(
            ["kilocode", "run", "--prompt", prompt, "--dir", self.project_path, "--auto", "--format", "default"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        try:
            for line in process.stdout:
                collect_output(line)

            process.wait(timeout=self.timeout_seconds)
            output = "\n".join(output_lines)
            passed = self._evaluate_pass(output, process.returncode)
            return passed, output
        except subprocess.TimeoutExpired:
            process.terminate()
            return False, "Review timed out after {} seconds".format(self.timeout_seconds)
        except Exception as e:
            process.terminate()
            return False, f"Review error: {e}"

    def _evaluate_pass(self, output: str, returncode: int) -> bool:
        """Evaluate pass/fail based on exit code + keyword analysis."""
        if returncode != 0:
            return False

        output_lower = output.lower()
        has_fail_keyword = any(kw in output_lower for kw in self.fail_keywords)
        has_pass_keyword = any(kw in output_lower for kw in self.pass_keywords)

        return has_pass_keyword and not has_fail_keyword


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

        # Group by category
        by_category: dict[str, list[dict]] = {}
        for rec in recommendations:
            cat = rec.get("category", "uncategorized")
            by_category.setdefault(cat, []).append(rec)

        # Collect all recommendations sorted by priority/impact
        priority_order = {"high": 0, "medium": 1, "low": 2}
        all_sorted = sorted(recommendations, key=lambda r: (priority_order.get(r.get("priority", "low"), 2), -r.get("impact", 0)))

        # Apply min_results per category, filling gaps from pool
        pool = list(all_sorted)
        result: list[dict] = []
        for cat, cat_recs in by_category.items():
            min_results = self.config.categories.get(cat, {}).get("min_results", 2)
            selected = cat_recs[:min_results]
            result.extend(selected)
            # Remove selected from pool so they don't appear twice
            for r in selected:
                if r in pool:
                    pool.remove(r)

        # If total < sum of all min_results, fill from pool (prioritized)
        total_min = sum(self.config.categories.get(cat, {}).get("min_results", 2) for cat in by_category)
        while len(result) < total_min and pool:
            result.append(pool.pop(0))

        self._last_scan = datetime.now()
        self._save(result)
        return result

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

        task_texts = [t.get("text", "") for t in data.get("completed", [])]

        feature_keywords = ["add ", "implement", "create new", "build new", "feature", "would be nice", "wish", "want", "should have", "missing", "create"]
        feature_requests = [(t, t.lower()) for t in task_texts if any(kw in t.lower() for kw in feature_keywords)]

        for original, _ in feature_requests[-3:]:
            # Extract a meaningful title from the request
            words = original.split()
            title = " ".join(words[:6]) + "..." if len(words) > 6 else original
            recommendations.append({
                "title": f"Feature request: {title}",
                "description": f'User requested: "{original[:150]}"',
                "priority": "medium",
                "impact": 65,
                "category": "feature_requests",
                "action": "Implement this feature or add it to the backlog"
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
                "title": f"Repeated pattern (x{repeats[0][1]}): {original[:50]}",
                "description": f'Task "{original}" has been requested {repeats[0][1]} times. This is a good candidate for automation.',
                "priority": "medium",
                "impact": 60,
                "category": "feature_requests",
                "action": f"Create an automated routine or /command for this repeated task"
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

        review_config = config.get("review", {})
        if review_config.get("enabled", KILOCODE_REVIEW_ENABLED):
            self.kilocode_reviewer = KiloCodeReviewer(
                project_path=self.project_path,
                timeout_seconds=review_config.get("timeout_seconds", KILOCODE_REVIEW_TIMEOUT_SECONDS),
                max_retries=review_config.get("max_retries", REVIEW_MAX_RETRIES_DEFAULT),
                pass_keywords=review_config.get("pass_keywords", REVIEW_PASS_KEYWORDS),
                fail_keywords=review_config.get("fail_keywords", REVIEW_FAIL_KEYWORDS),
            )
        else:
            self.kilocode_reviewer = None

        self.running = True
        self.streaming_message: dict = {}
        self._offset: Optional[int] = None
        self._task_semaphore = asyncio.Semaphore(1)
        self._task_lock = asyncio.Lock()
        self._last_kaizen_check: Optional[datetime] = None
        self._pending_kaizen_selection: Optional[str] = None  # chat_id của user đang chọn kaizen
        self._cached_recommendations: Optional[list[dict]] = None

        # Recover: clear stale running state from previous session
        if self.queue.has_running_task():
            data = self.queue._load()
            data["running"] = None
            self.queue._mark_dirty()
            self.queue._save(data)

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
        chat_id = str(update.message.chat_id)
        await self.bot.send_message(
            text="🔍 *Kaizen Scanner*\n\n⏳ Scanning codebase for improvements...",
            chat_id=chat_id,
        )

        recommendations = self.kaizen.scan()

        if not recommendations:
            await self.bot.send_message(
                text="✅ *Kaizen Scan Complete*\n\nNo improvements needed right now. Your code looks healthy!",
                chat_id=chat_id,
            )
            return

        # Store recommendations và chat_id để handle reply
        self._pending_kaizen_selection = chat_id
        self._cached_recommendations = recommendations

        # Send each recommendation as a separate formatted card
        for i, rec in enumerate(recommendations, 1):
            card = self.format_kaizen_card(rec, i, len(recommendations))
            await self.bot.send_message(text=card, chat_id=chat_id)

        # Prompt for selection
        count = len(recommendations)
        await self.bot.send_message(
            text=f"💡 *Reply with a number (1-{count})* to prioritize a task as your next request.",
            chat_id=chat_id,
        )

    @staticmethod
    def format_kaizen_card(rec: dict, index: int, total: int) -> str:
        """Format a single recommendation as a structured issue card."""
        priority = rec.get("priority", "medium").upper()
        category = rec.get("category", "general").replace("_", " ").title()
        impact = rec.get("impact", 50)

        # Priority emoji and color bar
        if priority == "HIGH":
            priority_icon = "🔴"
            bar = "▓▓▓▓▓"
        elif priority == "MEDIUM":
            priority_icon = "🟡"
            bar = "▓▓▓░░"
        else:
            priority_icon = "🟢"
            bar = "▓░░░░"

        title = rec.get("title", "Untitled")
        description = rec.get("description", "")
        action = rec.get("action", "Review and implement")

        # JIRA/GitHub style format
        card = [
            f"━━━━━━━━━━━━━━━━━━━━",
            f"#{index}/{total} | {priority_icon} *{priority}* | Impact: {bar} ({impact}/100)",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"📋 *{title}*",
            f"",
            f"🏷️ Category: `{category}`",
            f"",
            f"📖 *Description:*",
            f"{description}",
            f"",
            f"🎯 *Suggested Action:*",
            f"`{action}`",
        ]
        return "\n".join(card)

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
                        await self.bot.send_message(
                            text=f"🎯 *Executing: {selected['title']}*\n\n📖 {action}",
                            chat_id=chat_id,
                            reply_to_message_id=int(message_id),
                        )
                        asyncio.create_task(self._execute_task(message_id, chat_id, action))
                        return
                except (ValueError, IndexError):
                    # Not a valid number, fall through to normal task handling
                    pass

            if self.queue.has_running_task():
                self.queue.enqueue({"message_id": message_id, "text": text, "chat_id": chat_id})
                await self.bot.send_message(
                    text=f"📋 *Task queued*\n\n⏳ Position: {len(self.queue.peek())} in queue",
                    chat_id=chat_id,
                    reply_to_message_id=int(message_id),
                )
            else:
                self.queue.set_running({"message_id": message_id, "text": text, "chat_id": chat_id})
                await self.bot.send_message(
                    text="📋 *Starting task...*",
                    chat_id=chat_id,
                    reply_to_message_id=int(message_id),
                )
                asyncio.create_task(self._execute_task(message_id, chat_id, text))

    async def _execute_task(self, message_id: str, chat_id: str, text: str):
        await self._task_semaphore.acquire()
        stream_handler = StreamHandler(CHUNK_SIZE)
        run_path = self.project_path
        pending_edit_tasks: list = []

        try:
            # Phase 1: Starting
            streaming_msg = await self.bot.send_message(
                text="📋 *Starting task...*",
                chat_id=chat_id,
                reply_to_message_id=int(message_id),
            )
            self.streaming_message[message_id] = str(streaming_msg.message_id)
        except TelegramError as e:
            print(f"Failed to create streaming message: {e}", file=sys.stderr)
            self.queue.complete(message_id, False)
            self._task_semaphore.release()
            self._process_next()
            return

        # Phase 2: Running - update status
        await self.edit_message(chat_id, self.streaming_message.get(message_id, ""),
            "📋 *Starting task...*\n⏳ *Running...*")

        async def edit_output():
            chunks = "\n".join(stream_handler.buffer)
            if chunks:
                task = self.edit_message(chat_id, self.streaming_message.get(message_id, ""),
                    f"📋 *Starting task...*\n⏳ *Running...*\n\n```\n{chunks}\n```")
                pending_edit_tasks.append(task)
                return task
            return None

        async def send_error(line: str):
            task = self.send_text(chat_id, f"❌ {line}")
            pending_edit_tasks.append(task)
            return task

        claude = ClaudeSubprocess(run_path, self.timeout_minutes)

        def output_callback(line: str):
            chunks = stream_handler.add_line(line)
            if chunks:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(lambda: asyncio.create_task(edit_output()))

        def error_callback(line: str):
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(lambda: asyncio.create_task(send_error(line)))

        success = claude.run(text, output_callback, error_callback)

        remaining = stream_handler.flush()
        if remaining:
            await self.edit_message(chat_id, self.streaming_message.get(message_id, ""),
                f"📋 *Starting task...*\n⏳ *Running...*\n\n```\n" + "\n".join(remaining) + "\n```")

        # Wait for all scheduled edit tasks to complete
        if pending_edit_tasks:
            await asyncio.gather(*pending_edit_tasks, return_exceptions=True)

        self.queue.complete(message_id, success)

        # Phase 3: KiloCode review loop (retry if failed)
        if success and self.kilocode_reviewer:
            retry_count = 0
            last_review_output = ""
            last_review_passed = False

            while retry_count <= self.kilocode_reviewer.max_retries:
                retry_label = f" (attempt {retry_count + 1}/{self.kilocode_reviewer.max_retries + 1})" if retry_count > 0 else ""
                await self.edit_message(chat_id, self.streaming_message.get(message_id, ""),
                    f"✅ *Task completed*\n\n🔍 *Running KiloCode review...*{retry_label}")

                passed, review_output = self.kilocode_reviewer.review(text)

                if retry_count > 0:
                    last_review_output = f"[Retry {retry_count}] Previous issues:\n{last_review_output}\n\n---\nNew review:\n{review_output}"
                else:
                    last_review_output = review_output

                last_review_passed = passed

                if passed:
                    break

                retry_count += 1
                if retry_count > self.kilocode_reviewer.max_retries:
                    break

                # Retry Claude Code with FULL review feedback (no truncation)
                retry_msg = f"KiloCode review FAILED. Fix these issues:\n\n{review_output}\n\nFix and ensure code passes KiloCode review."
                await self.edit_message(chat_id, self.streaming_message.get(message_id, ""),
                    f"⚠️ *Review failed - retrying with full feedback...*")

                claude_retry = ClaudeSubprocess(run_path, self.timeout_minutes)
                retry_success = claude_retry.run(retry_msg, output_callback, error_callback)

                if not retry_success:
                    await self.edit_message(chat_id, self.streaming_message.get(message_id, ""),
                        f"❌ *Retry failed* - stopping after {retry_count} attempts")
                    break

            # Final review result - send in chunks if needed
            if last_review_passed:
                status = "✅ *Task completed & reviewed*\n📊 *KiloCode: PASSED*"
            else:
                status = "⚠️ *Task completed*\n📊 *KiloCode: Max retries reached*"

            await self.edit_message(chat_id, self.streaming_message.get(message_id, ""), status)
            self.streaming_message.pop(message_id, None)

            # Send review details in chunks (Telegram limit ~4096 chars per message)
            for i in range(0, len(last_review_output), TELEGRAM_MESSAGE_CHUNK_SIZE):
                chunk = last_review_output[i:i+TELEGRAM_MESSAGE_CHUNK_SIZE]
                part_label = f"📋 *Review details (part {i//TELEGRAM_MESSAGE_CHUNK_SIZE + 1}):*\n\n" if i > 0 else ""
                await self.send_text(chat_id, f"{part_label}```\n{chunk}\n```")
            self._task_semaphore.release()
            self._process_next()
            return

        # Phase 4: Final status (if no review)
        final_output = "\n".join(stream_handler.buffer) if stream_handler.buffer else ""
        if final_output:
            final_text = f"✅ *Task completed*\n\n```\n{final_output}\n```"
        elif not success:
            final_text = "❌ *Task failed* (no output captured)"
        else:
            final_text = "✅ *Task completed successfully*"
        await self.edit_message(chat_id, self.streaming_message.get(message_id, ""), final_text)
        self.streaming_message.pop(message_id, None)

        self._task_semaphore.release()
        self._process_next()

    def _process_next(self):
        """Pick up and execute the next pending task if any."""
        next_task = self.queue.dequeue()
        if next_task:
            asyncio.create_task(self._execute_task(
                next_task["message_id"],
                next_task["chat_id"],
                next_task["text"],
            ))

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
        await self._set_bot_commands()
        asyncio.create_task(self._periodic_kaizen_check())
        # Process any pending tasks from previous session
        self._process_next()
        await self.poll_loop()

    async def _set_bot_commands(self):
        """Set bot menu commands."""
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show help information"),
            BotCommand("status", "Show current queue status"),
            BotCommand("kaizen", "Scan for improvement recommendations"),
        ]
        await self.bot.set_my_commands(commands)

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

    # Review config (KiloCode post-task review)
    review_cfg = config.get("review", {})
    config["review"] = {
        "enabled": os.environ.get("KILOCODE_REVIEW_ENABLED", "true").lower() == "true",
        "max_retries": int(os.environ.get("KILOCODE_REVIEW_MAX_RETRIES", review_cfg.get("max_retries", REVIEW_MAX_RETRIES_DEFAULT))),
        "timeout_seconds": int(os.environ.get("KILOCODE_REVIEW_TIMEOUT", review_cfg.get("timeout_seconds", KILOCODE_REVIEW_TIMEOUT_SECONDS))),
    }

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
