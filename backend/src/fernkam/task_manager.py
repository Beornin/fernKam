"""DB-backed task manager for tracking background operations.

Tasks are written to the `tasks` table and cached in-memory for fast reads.
On restart, running tasks are loaded from DB so they remain visible.
"""
from __future__ import annotations

import asyncio
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


_MAX_CACHED_TERMINAL_TASKS = 300


# Tasks that walk, move, copy or delete files in the library. Two of these at
# once is the one combination that actually corrupts state: two concurrent
# scans created 2,833 duplicate photo rows (see migration 0026), and a scan
# racing a file-move sees files mid-flight and can delete rows for files that
# merely moved.
#
# Deliberately narrow. Embedding, search, face work and geocoding only *read*
# files, so they keep running alongside — serialising everything would mean
# not being able to search during a 20-minute embed run, which is a worse app
# for no safety gain.
FILE_MUTATING_TASKS = frozenset({
    "scan_library",
    "workflow_sorting",
    "workflow_remove_nonkeep_raw",
    "workflow_move_raws",
    "workflow_pureraw",   # writes JPGs into AA_RAW; Remove non-keep RAW mid-run would bin NEFs not developed yet
})

# Tasks that saturate the GPU. Two at once (a model index and PureRAW, say)
# overflow 24 GB of VRAM into system RAM and both crawl, so they take turns.
GPU_TASKS = frozenset({"model_install", "embed_photos", "vision_check", "workflow_pureraw"})


def _clash(a: str, b: str) -> bool:
    return (a in FILE_MUTATING_TASKS and b in FILE_MUTATING_TASKS) or (a in GPU_TASKS and b in GPU_TASKS)


def fmt_eta(seconds: float) -> str:
    """ETA for task messages: '<1 min', '12 min', '4 h 05 min'."""
    m = round(seconds / 60)
    if m < 1:
        return "<1 min"
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


class TaskConflict(RuntimeError):
    """Raised when a file-mutating task is started while another is running.

    Carries the offending task so the caller can name it. Mapped to HTTP 409 by
    a handler in api/app.py, so every router gets the behaviour without a guard
    at each call site — including routers written later.
    """

    def __init__(self, running: "Task") -> None:
        self.running = running
        super().__init__(
            f"'{running.task_type}' is already running — wait for it to finish "
            f"before starting another job that changes files or needs the GPU."
        )


class TaskManager:
    """DB-backed task manager with in-memory cache."""

    def __init__(self):
        self._cache: dict[str, Task] = {}
        # File-mutating tasks whose work has not finished yet, whatever their
        # status says. Cancelling only flips the status; a workflow running
        # in a thread cannot be interrupted, so the guard must hold until the
        # work reports completed/failed, not until someone clicks Cancel.
        self._busy_file_tasks: dict[str, Task] = {}
        self._create_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune_cache(self) -> None:
        """Cap the cache: keep every running task plus the most recent
        completed/failed/cancelled ones, evicting the rest.

        Every create_task()/get_task()/get_all_tasks() call adds an entry
        that was otherwise never removed — a long-running server accumulates
        one permanent Task object per background operation ever run (scans,
        syncs, geocodes, ...) for the life of the process.
        """
        terminal = [t for t in self._cache.values() if t.status != "running"]
        if len(terminal) <= _MAX_CACHED_TERMINAL_TASKS:
            return
        terminal.sort(key=lambda t: t.completed_at or t.started_at)
        for t in terminal[: len(terminal) - _MAX_CACHED_TERMINAL_TASKS]:
            self._cache.pop(t.id, None)

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
        """Create a new task, persist to DB, return its ID.

        Raises TaskConflict if `task_type` mutates files and another
        file-mutating task is already running, or needs the GPU while another
        GPU task runs. Enforced here rather than at each call site so a new
        endpoint cannot forget it.
        """
        if task_type not in FILE_MUTATING_TASKS and task_type not in GPU_TASKS:
            return await self._insert_task(task_type, message)
        # The check and the insert must be atomic: get_running_tasks() awaits
        # the database, so two clicks in quick succession could both pass the
        # check before either row existed.
        async with self._create_lock:
            if task_type in FILE_MUTATING_TASKS:
                self.ensure_no_file_task()
            for running in await self.get_running_tasks():
                if _clash(task_type, running.task_type):
                    raise TaskConflict(running)
            task_id = await self._insert_task(task_type, message)
            if task_type in FILE_MUTATING_TASKS:
                self._busy_file_tasks[task_id] = self._cache[task_id]
            return task_id

    def ensure_no_file_task(self) -> None:
        """Raise TaskConflict if a file-mutating task's work is still running
        in this process. For endpoints that move files synchronously (not as a
        task) but must not race a scan."""
        for busy in self._busy_file_tasks.values():
            raise TaskConflict(busy)

    async def _insert_task(self, task_type: str, message: str) -> str:
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

        self._prune_cache()
        return task_id

    async def update_task(self, task_id: str, status: Optional[str] = None,
                          message: Optional[str] = None, progress: Optional[dict] = None):
        """Update task in cache and DB."""
        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import update

        task = self._cache.get(task_id)
        if task is None:
            return

        # The work itself has stopped when it reports completed/failed, or
        # re-reports "cancelled" to acknowledge a cancel (cancel_task() sets
        # the first "cancelled" while the work may still be running).
        if status in ("completed", "failed") or (status == "cancelled" and task.status == "cancelled"):
            self._busy_file_tasks.pop(task_id, None)

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

        if status in ("completed", "failed", "cancelled"):
            self._prune_cache()

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
                self._prune_cache()
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

    async def cancel_stale_tasks(self) -> int:
        """Mark all running tasks as cancelled (call once at startup).

        Running tasks from a previous process will never complete; leaving them
        as 'running' causes the frontend to spin indefinitely.
        """
        from fernkam.db.models.tasks import BackgroundTask
        from sqlalchemy import update, select

        now = datetime.now(timezone.utc)
        try:
            async with await self._db_session() as db:
                rows = (await db.execute(
                    select(BackgroundTask).where(BackgroundTask.status == "running")
                )).scalars().all()
                count = len(rows)
                if count:
                    await db.execute(
                        update(BackgroundTask)
                        .where(BackgroundTask.status == "running")
                        .values(status="cancelled",
                                message="Cancelled: server restarted",
                                completed_at=now)
                    )
                    await db.commit()
                    for row in rows:
                        if row.id in self._cache:
                            self._cache[row.id].status = "cancelled"
                return count
        except Exception as exc:
            print(f"[task_manager] cancel_stale_tasks error: {exc}", flush=True)
            return 0

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
