"""DB-backed task manager for tracking background operations.

Tasks are written to the `tasks` table and cached in-memory for fast reads.
On restart, running tasks are loaded from DB so they remain visible.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    task_type: str
    status: str  # "running", "completed", "failed", "cancelled"
    message: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    progress: Optional[dict] = None


class TaskManager:
    """DB-backed task manager with in-memory cache."""

    def __init__(self):
        self._cache: dict[str, Task] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_task(self, row) -> Task:
        return Task(
            id=row.id,
            task_type=row.task_type,
            status=row.status,
            message=row.message,
            started_at=row.started_at,
            completed_at=row.completed_at,
            progress=row.progress,
        )

    async def _db_session(self):
        from fernkam.db.session import async_session_factory
        return async_session_factory()

    # ------------------------------------------------------------------
    # Public API (all async)
    # ------------------------------------------------------------------

    async def create_task(self, task_type: str, message: str) -> str:
        """Create a new task, persist to DB, return its ID."""
        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import insert

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        task = Task(id=task_id, task_type=task_type, status="running",
                    message=message, started_at=now)
        self._cache[task_id] = task

        try:
            async with await self._db_session() as db:
                await db.execute(
                    insert(BackgroundTask).values(
                        id=task_id, task_type=task_type, status="running",
                        message=message, started_at=now,
                    )
                )
                await db.commit()
        except Exception:
            pass  # cache still valid; DB write best-effort

        return task_id

    async def update_task(self, task_id: str, status: Optional[str] = None,
                          message: Optional[str] = None, progress: Optional[dict] = None):
        """Update task in cache and DB."""
        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import update

        task = self._cache.get(task_id)
        if task is None:
            return

        now = datetime.now(timezone.utc)
        values: dict = {}
        if status:
            task.status = status
            values["status"] = status
            if status in ("completed", "failed", "cancelled"):
                task.completed_at = now
                values["completed_at"] = now
        if message is not None:
            task.message = message
            values["message"] = message
        if progress is not None:
            task.progress = progress
            values["progress"] = progress

        if not values:
            return

        try:
            async with await self._db_session() as db:
                await db.execute(
                    update(BackgroundTask).where(BackgroundTask.id == task_id).values(**values)
                )
                await db.commit()
        except Exception:
            pass

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Return from cache; fall back to DB."""
        if task_id in self._cache:
            return self._cache[task_id]

        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import select

        try:
            async with await self._db_session() as db:
                row = (await db.execute(
                    select(BackgroundTask).where(BackgroundTask.id == task_id)
                )).scalar_one_or_none()
                if row:
                    task = self._to_task(row)
                    self._cache[task_id] = task
                    return task
        except Exception:
            pass
        return None

    async def get_all_tasks(self) -> list[Task]:
        """Fetch all tasks from DB (most recent 200), merge with cache."""
        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import select

        try:
            async with await self._db_session() as db:
                rows = (await db.execute(
                    select(BackgroundTask)
                    .order_by(BackgroundTask.started_at.desc())
                    .limit(200)
                )).scalars().all()
                tasks = [self._to_task(r) for r in rows]
                for t in tasks:
                    self._cache[t.id] = t
                return tasks
        except Exception:
            return list(self._cache.values())

    async def get_running_tasks(self) -> list[Task]:
        """Return running tasks from DB."""
        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import select

        try:
            async with await self._db_session() as db:
                rows = (await db.execute(
                    select(BackgroundTask).where(BackgroundTask.status == "running")
                )).scalars().all()
                return [self._to_task(r) for r in rows]
        except Exception:
            return [t for t in self._cache.values() if t.status == "running"]

    async def cancel_task(self, task_id: str) -> bool:
        """Mark a task as cancelled. Returns True if it existed and was running."""
        task = self._cache.get(task_id)
        if task is None:
            task = await self.get_task(task_id)
        if task is None or task.status != "running":
            return False
        await self.update_task(task_id, status="cancelled")
        return True


# Global task manager instance
task_manager = TaskManager()
