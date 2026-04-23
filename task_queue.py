import json
from datetime import datetime
from pathlib import Path


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

    def peek(self) -> dict | None:
        data = self._load()
        if data["pending"]:
            return data["pending"][0]
        return None

    def dequeue(self) -> dict | None:
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