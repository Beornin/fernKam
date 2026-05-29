"""Simple in-memory task manager for tracking background operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    task_type: str
    status: str  # "running", "completed", "failed"
    message: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    progress: Optional[dict] = None


class TaskManager:
    """In-memory task manager for tracking background operations."""
    
    def __init__(self):
        self.tasks: dict[str, Task] = {}
    
    def create_task(self, task_type: str, message: str) -> str:
        """Create a new task and return its ID."""
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = Task(
            id=task_id,
            task_type=task_type,
            status="running",
            message=message
        )
        return task_id
    
    def update_task(self, task_id: str, status: Optional[str] = None, message: Optional[str] = None, progress: Optional[dict] = None):
        """Update an existing task."""
        if task_id not in self.tasks:
            return
        task = self.tasks[task_id]
        if status:
            task.status = status
            if status in ["completed", "failed"]:
                task.completed_at = datetime.now(timezone.utc)
        if message:
            task.message = message
        if progress:
            task.progress = progress
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self.tasks.get(task_id)
    
    def get_all_tasks(self) -> list[Task]:
        """Get all tasks."""
        return list(self.tasks.values())
    
    def get_running_tasks(self) -> list[Task]:
        """Get all running tasks."""
        return [t for t in self.tasks.values() if t.status == "running"]


# Global task manager instance
task_manager = TaskManager()
