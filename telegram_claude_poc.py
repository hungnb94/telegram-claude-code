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
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from telegram import Bot, Update
from telegram.error import TelegramError

SCRIPT_DIR = Path(__file__).parent
TASKS_FILE = SCRIPT_DIR / "tasks.json"
CHUNK_SIZE = 30


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
            ["claude", "code", "--print", "--dangerously-skip-permissions", "--no-session-persistence"],
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
        self.queue = TaskQueue(TASKS_FILE)
        self.running = True
        self.streaming_message: dict = {}
        self._offset: Optional[int] = None
        self._task_lock = asyncio.Lock()

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
            "Claude Code Telegram Bot\n\nSend me a task description and I'll execute it using Claude Code on your local codebase.\n\nTasks are queued if a task is already running."
        )

    async def handle_message(self, update: Update):
        if update.message and update.message.text:
            text = update.message.text.strip()
            chat_id = str(update.message.chat_id)
            message_id = str(update.message.message_id)

            if self.queue.has_running_task():
                self.queue.enqueue({"message_id": message_id, "text": text, "chat_id": chat_id})
                await update.message.reply_text(f"Task queued (position: {len(self.queue.peek())})")
            else:
                self.queue.set_running({"message_id": message_id, "text": text, "chat_id": chat_id})
                await update.message.reply_text("Starting task...")
                asyncio.create_task(self._execute_task(message_id, chat_id, text))

    async def _execute_task(self, message_id: str, chat_id: str, text: str):
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

            claude = ClaudeSubprocess(self.project_path, self.timeout_minutes)
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
                updates = await self.bot.get_updates(timeout=self.poll_interval, offset=self._offset)
                if updates:
                    for update in updates:
                        if update.message:
                            if update.message.text == "/start":
                                await self.handle_start(update)
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
        await self.poll_loop()


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
