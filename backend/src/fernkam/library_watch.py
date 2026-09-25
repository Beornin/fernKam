"""Watch the library for changes made while fernKam is running.

digiKam's "Monitor the albums for external changes": when another program
edits, adds, moves or deletes photos while fernKam is open, fernKam notices
and catches up without a restart or a manual rescan.

Events are only a hint of *where* to look. After a quiet period, the normal
library scan runs over the smallest folder covering everything that changed,
so every rule the scan follows applies unchanged: the three-way metadata merge,
picture-change detection, move/rename matching, the removal safeguards, and
the one-file-job-at-a-time guard (if a workflow or another scan is running,
the changes wait and are picked up when it finishes).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Iterable, Optional

from fernkam.media_types import ALL_EXTENSIONS

logger = logging.getLogger(__name__)

# Rust-side timeout: with yield_on_timeout the watcher wakes this often even
# without events, which is when changes held back by a running job are retried.
_IDLE_TICK_MS = 5_000

# Set on shutdown; the watcher thread checks it every step.
_stop = threading.Event()


def is_relevant(path: str) -> bool:
    """Media files and suffix-less paths (possibly folders —
    Windows reports a deleted or moved folder once, not per file). Hidden
    folders and tool temp files (exiftool's `*_exiftool_tmp`) are ignored."""
    p = Path(path)
    if any(part.startswith(".") for part in p.parts[-4:]):
        return False
    ext = p.suffix.lower()
    return ext in ALL_EXTENSIONS or ext == ""


def scan_scope(paths: Iterable[str], root: Path) -> Optional[Path]:
    """The smallest existing folder inside `root` that contains every change.

    Uses each path's parent: a deleted file or folder no longer exists, and a
    move shows up as a removal in one folder plus an addition in another —
    scanning their common ancestor in one pass is what lets the scan match
    the two and keep the photo's row.
    """
    parents = []
    for raw in paths:
        p = Path(raw).parent
        try:
            p.relative_to(root)
        except ValueError:
            continue
        parents.append(p)
    if not parents:
        return None
    scope = Path(os.path.commonpath([str(p) for p in parents]))
    while scope != root and not scope.is_dir():  # a deleted folder: walk up
        scope = scope.parent
    return scope


def _watch_thread(root: Path, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue,
                  watch_filter, polling: bool) -> None:
    """Blocking watcher, run in its own daemon thread.

    watchfiles' async awatch() runs this same watcher through AnyIO's thread
    pool, whose worker threads are not daemons and, under Granian, are never
    told to stop — the worker process then hung at exit, so closing the
    launcher window never finished. A daemon thread of our own can't block
    interpreter exit, and _stop ends it within one step anyway.
    """
    from watchfiles import watch
    try:
        for changes in watch(
            root, watch_filter=watch_filter, debounce=5_000, step=1_500,
            stop_event=_stop, rust_timeout=_IDLE_TICK_MS, yield_on_timeout=True,
            force_polling=polling or None, ignore_permission_denied=True,
            raise_interrupt=False,
        ):
            loop.call_soon_threadsafe(queue.put_nowait, changes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[watch] stopped watching %s: %s", root, exc)
    finally:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except RuntimeError:
            pass  # the event loop is already closed


async def watch_library() -> None:
    """Run until stop() is called. Never raises: a watcher that can't start
    (unsupported filesystem, too many folders for inotify) only logs."""
    from fernkam.api.routers.sync.library import start_library_scan
    from fernkam.config import get_settings
    from fernkam.task_manager import TaskConflict

    settings = get_settings()
    root = Path(settings.library_root)
    if not root.is_dir():
        logger.warning("[watch] LIBRARY_ROOT %s not found; not watching for outside changes", root)
        return
    thumbs = Path(settings.thumb_cache_dir)

    def _filter(_change, path: str) -> bool:
        return is_relevant(path) and not Path(path).is_relative_to(thumbs)

    queue: asyncio.Queue = asyncio.Queue()
    _stop.clear()
    threading.Thread(
        target=_watch_thread, name="fernkam-library-watch", daemon=True,
        args=(root, asyncio.get_running_loop(), queue, _filter, settings.watch_library_polling),
    ).start()

    pending: set[str] = set()
    print(f"[watch] Watching {root} for changes made outside fernKam", flush=True)
    while (changes := await queue.get()) is not None:
        pending.update(path for _change, path in changes)
        if not pending:
            continue
        scope = scan_scope(pending, root)
        if scope is None:
            pending.clear()
            continue
        rel = scope.relative_to(root).as_posix() if scope != root else "library"
        try:
            await start_library_scan(
                custom_path=None if scope == root else str(scope),
                label=f"Picking up outside changes in {rel}…",
            )
            logger.info("[watch] %d change(s) → scanning %s", len(pending), rel)
            pending.clear()
        except TaskConflict:
            pass  # a scan or workflow is running; retry on the next tick
        except Exception as exc:  # noqa: BLE001
            logger.warning("[watch] could not start a scan for %s: %s", rel, exc)


def stop() -> None:
    _stop.set()
