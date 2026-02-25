"""Background task runner using threading."""

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class TaskInfo:
    """Information about a background task."""
    id: str
    name: str
    status: str = "pending"  # pending | running | completed | failed
    message: str = ""
    result: Any = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskManager:
    """Manages background tasks using threading."""

    def __init__(self, cleanup_after_seconds: int = 3600):
        self._tasks: dict[str, TaskInfo] = {}
        self._lock = threading.Lock()
        self._cleanup_after = cleanup_after_seconds

    def start_task(
        self,
        name: str,
        func: Callable,
        *args,
        **kwargs,
    ) -> str:
        """Start a background task.

        Args:
            name: Human-readable task name.
            func: The function to execute. It receives a `progress_callback`
                  keyword argument that accepts a string message.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            Task ID string.
        """
        self._cleanup_old_tasks()

        task_id = uuid.uuid4().hex[:12]
        task = TaskInfo(id=task_id, name=name, status="running", started_at=datetime.now())

        with self._lock:
            self._tasks[task_id] = task

        def progress_callback(message: str):
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].message = message

        def wrapper():
            try:
                result = func(*args, progress_callback=progress_callback, **kwargs)
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].status = "completed"
                        self._tasks[task_id].result = result
                        self._tasks[task_id].completed_at = datetime.now()
            except Exception as e:
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].status = "failed"
                        self._tasks[task_id].error = str(e)
                        self._tasks[task_id].completed_at = datetime.now()

        thread = threading.Thread(target=wrapper, daemon=True)
        thread.start()

        return task_id

    def get_task(self, task_id: str) -> TaskInfo | None:
        """Get task info by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def has_running_task(self, name: str | None = None) -> bool:
        """Check if there's a running task, optionally filtering by name."""
        with self._lock:
            for task in self._tasks.values():
                if task.status == "running":
                    if name is None or task.name == name:
                        return True
        return False

    def _cleanup_old_tasks(self):
        """Remove tasks older than cleanup_after_seconds."""
        cutoff = time.time() - self._cleanup_after
        with self._lock:
            to_remove = []
            for task_id, task in self._tasks.items():
                if task.completed_at and task.completed_at.timestamp() < cutoff:
                    to_remove.append(task_id)
            for task_id in to_remove:
                del self._tasks[task_id]
