"""Start background work without losing it.

asyncio keeps only a weak reference to a task, so `asyncio.create_task(job())` with nothing holding
the result can be garbage-collected in the middle of a scan, a write-back or a learning run (the
Python docs warn of exactly this). spawn() holds the task until it finishes, and logs a crash that
nobody awaits instead of leaving "Task exception was never retrieved" on stderr.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional

logger = logging.getLogger(__name__)

_running: set[asyncio.Task] = set()


def _done(task: asyncio.Task) -> None:
    _running.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("background task %s crashed", task.get_name(), exc_info=task.exception())


def spawn(coro: Coroutine[Any, Any, Any], *, name: Optional[str] = None) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_done)
    return task
