"""Async log sink: queues records from the Python logging handler and the
native stderr capture, then flushes to the `app_logs` table in batches.

Design:
- Producers (Python LogHandler in any thread; stderr reader thread) call
  `enqueue_record(dict)`. This uses asyncio.run_coroutine_threadsafe to push
  onto a bounded queue on the main event loop. If the queue is full, the
  record is dropped and a counter incremented (so a flood never blocks the app).
- A single async drainer task wakes up every FLUSH_INTERVAL seconds OR when
  BATCH_SIZE records are queued, and flushes them.
- Coalesce: rows sharing a fingerprint inside COALESCE_WINDOW_S are merged into
  the most recent existing row (UPDATE occurrences/last_seen_at) instead of
  inserting a new row. Reduces volume by 100x+ for spammy native warnings.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --- runtime configuration --------------------------------------------------
QUEUE_MAX = 10_000
BATCH_SIZE = 200
FLUSH_INTERVAL_S = 0.25
COALESCE_WINDOW_S = 5.0
RETENTION_DAYS = int(os.getenv("FERNKAM_LOG_RETENTION_DAYS", "30"))


# Context propagated by `with log_context(task_id=..., photo_id=...):`
_log_context: contextvars.ContextVar[dict] = contextvars.ContextVar("_log_context", default={})


class log_context:
    """Push a dict onto the contextvar; restored on exit."""
    def __init__(self, **kw: Any) -> None:
        self._kw = kw
        self._token = None

    def __enter__(self):
        cur = _log_context.get()
        merged = {**cur, **self._kw}
        self._token = _log_context.set(merged)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _log_context.reset(self._token)


def current_log_context() -> dict:
    return dict(_log_context.get())


# --- queue + main loop reference --------------------------------------------
_queue: Optional[asyncio.Queue] = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_drainer_task: Optional[asyncio.Task] = None
_dropped = 0
_inserted_total = 0
_updated_total = 0
_last_flush_ms = 0


def _normalize_for_fingerprint(msg: str) -> str:
    """Strip volatile bits (numbers, paths, timestamps) so repeated warnings
    that differ only in numeric arguments collapse to the same fingerprint."""
    s = msg
    # OpenCV: "[ WARN:0@227843.698] global …"
    s = re.sub(r"\[\s*\w+:\d+@\d+(?:\.\d+)?\]\s*", "", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "N", s)
    s = re.sub(r"\b0x[0-9a-fA-F]+\b", "0xN", s)
    s = re.sub(r"[A-Za-z]:\\[^\s]+", "PATH", s)
    s = re.sub(r"/[^\s]+", "PATH", s)
    return s.strip()[:512]


def _make_fingerprint(level: int, logger_name: Optional[str], message: str) -> str:
    raw = f"{level}|{logger_name or ''}|{_normalize_for_fingerprint(message)}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def enqueue_record(rec: dict) -> None:
    """Thread-safe enqueue. Drops if queue is full."""
    global _dropped
    if _queue is None or _main_loop is None:
        return
    if "fingerprint" not in rec:
        rec["fingerprint"] = _make_fingerprint(
            rec.get("level", logging.INFO),
            rec.get("logger_name"),
            rec.get("message", ""),
        )
    rec.setdefault("occurrences", 1)
    rec.setdefault("ts", datetime.now(timezone.utc))
    rec.setdefault("context", current_log_context() or None)
    try:
        _main_loop.call_soon_threadsafe(_queue.put_nowait, rec)
    except RuntimeError:
        # Loop is closed
        return
    except asyncio.QueueFull:
        _dropped += 1


async def _flush_batch(batch: list[dict]) -> None:
    """INSERT or UPDATE-coalesce a batch. Owns its own AsyncSession."""
    global _inserted_total, _updated_total, _last_flush_ms
    if not batch:
        return
    from fernkam.db.session import async_session_factory
    from sqlalchemy import text

    t0 = time.perf_counter()
    async with async_session_factory() as db:
        # Group by fingerprint so duplicates inside this batch already coalesce.
        by_fp: dict[str, list[dict]] = {}
        for r in batch:
            by_fp.setdefault(r["fingerprint"], []).append(r)

        for fp, rows in by_fp.items():
            n = len(rows)
            latest = rows[-1]

            # Try to extend a recent existing row with the same fingerprint.
            existing = (await db.execute(text(
                """
                SELECT id, last_seen_at FROM app_logs
                WHERE fingerprint = :fp
                  AND last_seen_at >= now() - (:window || ' seconds')::interval
                ORDER BY last_seen_at DESC
                LIMIT 1
                """
            ), {"fp": fp, "window": str(COALESCE_WINDOW_S)})).first()

            if existing is not None:
                await db.execute(text(
                    """
                    UPDATE app_logs
                       SET occurrences = occurrences + :n,
                           last_seen_at = now(),
                           message = :msg
                     WHERE id = :id
                    """
                ), {"n": n, "id": existing.id, "msg": latest["message"]})
                _updated_total += 1
                continue

            await db.execute(text(
                """
                INSERT INTO app_logs
                    (ts, level, level_name, source, logger_name, message,
                     file, line, func, exc_info, context, fingerprint,
                     occurrences, last_seen_at)
                VALUES
                    (:ts, :level, :level_name, :source, :logger_name, :message,
                     :file, :line, :func, :exc_info,
                     CAST(:context AS jsonb), :fingerprint,
                     :occurrences, :ts)
                """
            ), {
                "ts": latest["ts"],
                "level": latest.get("level", logging.INFO),
                "level_name": latest.get("level_name", "INFO"),
                "source": latest.get("source", "python"),
                "logger_name": latest.get("logger_name"),
                "message": latest["message"],
                "file": latest.get("file"),
                "line": latest.get("line"),
                "func": latest.get("func"),
                "exc_info": latest.get("exc_info"),
                "context": json.dumps(latest.get("context") or None) if latest.get("context") else None,
                "fingerprint": fp,
                "occurrences": n,
            })
            _inserted_total += 1

        await db.commit()
    _last_flush_ms = int((time.perf_counter() - t0) * 1000)


async def _drainer() -> None:
    assert _queue is not None
    pending: list[dict] = []
    last_flush = time.monotonic()
    while True:
        try:
            timeout = max(0.0, FLUSH_INTERVAL_S - (time.monotonic() - last_flush))
            try:
                rec = await asyncio.wait_for(_queue.get(), timeout=timeout or 0.001)
                pending.append(rec)
            except asyncio.TimeoutError:
                pass

            if pending and (
                len(pending) >= BATCH_SIZE
                or (time.monotonic() - last_flush) >= FLUSH_INTERVAL_S
            ):
                try:
                    await _flush_batch(pending)
                except Exception as e:
                    # Avoid recursive logging into ourselves
                    print(f"[logs_sink] flush error: {e}", flush=True)
                pending.clear()
                last_flush = time.monotonic()
        except asyncio.CancelledError:
            try:
                if pending:
                    await _flush_batch(pending)
            except Exception:
                pass
            return


async def start_sink() -> None:
    """Initialize queue + drainer on the current event loop."""
    global _queue, _main_loop, _drainer_task
    if _drainer_task is not None and not _drainer_task.done():
        return
    _main_loop = asyncio.get_running_loop()
    _queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _drainer_task = asyncio.create_task(_drainer(), name="fernkam-logs-drainer")
    logger.info("[logs_sink] started")


async def stop_sink() -> None:
    global _drainer_task
    if _drainer_task and not _drainer_task.done():
        _drainer_task.cancel()
        try:
            await _drainer_task
        except Exception:
            pass
    _drainer_task = None


def sink_stats() -> dict:
    return {
        "queued": _queue.qsize() if _queue is not None else 0,
        "inserted_total": _inserted_total,
        "updated_total": _updated_total,
        "dropped_total": _dropped,
        "last_flush_ms": _last_flush_ms,
        "running": _drainer_task is not None and not _drainer_task.done(),
    }


# --- Python logging handler -------------------------------------------------

class PostgresLogHandler(logging.Handler):
    """logging.Handler that pushes records to the async sink."""

    # Self-protection: avoid recursive logging from these noisy loggers.
    _SKIP_PREFIXES = ("fernkam.logs_sink", "sqlalchemy.engine", "sqlalchemy.pool")

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            if any(record.name.startswith(p) for p in self._SKIP_PREFIXES):
                return
            msg = record.getMessage()
            exc_info = None
            if record.exc_info:
                exc_info = self.formatter.formatException(record.exc_info) if self.formatter else None
                if exc_info is None:
                    import traceback
                    exc_info = "".join(traceback.format_exception(*record.exc_info))
            enqueue_record({
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc),
                "level": record.levelno,
                "level_name": record.levelname,
                "source": "python",
                "logger_name": record.name,
                "message": msg,
                "file": record.pathname,
                "line": record.lineno,
                "func": record.funcName,
                "exc_info": exc_info,
            })
        except Exception:
            # Never raise from a logging handler.
            self.handleError(record)


def install_python_handler(min_level: int = logging.WARNING) -> PostgresLogHandler:
    """Attach the handler to the root logger."""
    h = PostgresLogHandler(level=min_level)
    h.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    # Avoid duplicate installs.
    for existing in root.handlers:
        if isinstance(existing, PostgresLogHandler):
            return existing
    root.addHandler(h)
    return h


# --- retention --------------------------------------------------------------

async def prune_old_logs() -> int:
    from fernkam.db.session import async_session_factory
    from sqlalchemy import text

    days = max(1, RETENTION_DAYS)
    async with async_session_factory() as db:
        res = await db.execute(text(
            "DELETE FROM app_logs WHERE ts < now() - (:d || ' days')::interval"
        ), {"d": str(days)})
        await db.commit()
    return int(res.rowcount or 0)


async def retention_loop() -> None:
    """Run prune_old_logs every 24 h; cancellable via task name."""
    while True:
        try:
            n = await prune_old_logs()
            if n:
                logger.info("[logs_sink] pruned %d old log rows", n)
        except Exception as e:
            print(f"[logs_sink] retention error: {e}", flush=True)
        await asyncio.sleep(24 * 3600)
