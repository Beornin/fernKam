"""Capture native stderr (OpenCV/ffmpeg/onnxruntime/etc.) and tee it to both
the original terminal AND the async log sink.

Implementation: on `install()`, dup the original FD 2, replace it with the
write-end of an OS pipe. A background thread reads lines from the pipe, writes
them back to the original FD 2 (so the terminal still gets them), then enqueues
a structured log record.

Important: import this BEFORE cv2/onnxruntime/insightface so they pick up the
replaced FD when their native libs cache it on first use.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_orig_stderr_fd: Optional[int] = None
_pipe_read_fd: Optional[int] = None
_pipe_write_fd: Optional[int] = None
_reader_thread: Optional[threading.Thread] = None
_installed = False


_LEVEL_RE = re.compile(r"\b(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\b", re.IGNORECASE)

_STDERR_BLOCKLIST = re.compile(
    r"grfmt_tiff\.cpp"
    r"|TIFFReadDirectory"
    r"|tag 40094|0x9c9e"
    r"|Internal imread issue"
    r"|original_ptr.*real_mat"
    r"|real_mat.*original_ptr"
    r"|loadsave\.cpp.*imread_.*can't read data"
    r"|imread_\(.*\).*can't read data",
    re.IGNORECASE,
)


def _guess_level(line: str) -> tuple[int, str]:
    m = _LEVEL_RE.search(line)
    if not m:
        return logging.INFO, "INFO"
    tok = m.group(1).upper()
    if tok in ("FATAL",):
        return logging.CRITICAL, "CRITICAL"
    if tok == "ERROR":
        return logging.ERROR, "ERROR"
    if tok in ("WARN", "WARNING"):
        return logging.WARNING, "WARNING"
    if tok == "DEBUG":
        return logging.DEBUG, "DEBUG"
    return logging.INFO, "INFO"


# A line that continues the record before it: a traceback ("Traceback...", indented frames, the
# closing "SomeError: message"), a chained-exception note, or SQLAlchemy's "(Background on...)".
_CONTINUATION_RE = re.compile(
    r"^(?:\s|Traceback \(|During handling of|The above exception|\(Background on this error"
    r"|[\w.]+(?:Error|Exception|Exit|Interrupt|Warning)\b)")
# A Python logging line ("ERROR [fernkam.error] ...", uvicorn's "ERROR:  ..."): a traceback that follows
# one belongs to it; a traceback after any other line starts its own record.
_LOGGER_HEAD_RE = re.compile(r"^(?:DEBUG|INFO|WARNI(?:NG)?|ERROR|CRITI(?:CAL)?)\b")
# What the Python logging handler already stores (with its own traceback): its terminal copy
# ("WARNI [fernkam.x] ...", "ERROR [fernkam.error] ...") would only be a duplicate row.
_PYTHON_LOGGED_RE = re.compile(r"^(?:WARNI|WARNING|ERROR|CRITI|CRITICAL)\s+\[fernkam[\w.]*\]")
_GROUP_IDLE_S = 0.25      # a record with nothing more coming for this long is complete
_GROUP_MAX_CHARS = 20_000


class _Grouper:
    """Joins a traceback's lines into the one record they are, instead of one row per line."""

    def __init__(self, emit) -> None:
        self._emit = emit
        self._lines: list[str] = []
        self._chars = 0
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def feed(self, line: str) -> None:
        with self._lock:
            if self._lines and self._chars < _GROUP_MAX_CHARS and self._continues(line):
                self._lines.append(line)
                self._chars += len(line)
            else:
                self._flush_locked()
                self._lines, self._chars = [line], len(line)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(_GROUP_IDLE_S, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def _continues(self, line: str) -> bool:
        if line.startswith("Traceback ("):
            in_traceback = any(l.startswith("Traceback (") for l in self._lines)   # a chained exception
            return in_traceback or (len(self._lines) == 1 and bool(_LOGGER_HEAD_RE.match(self._lines[0])))
        return bool(_CONTINUATION_RE.match(line))

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        lines, self._lines, self._chars = self._lines, [], 0
        if lines:
            try:
                self._emit(lines)
            except Exception:
                pass


def _emit_group(lines: list[str]) -> None:
    from fernkam import logs_sink

    head = lines[0]
    if _STDERR_BLOCKLIST.search(head) or _PYTHON_LOGGED_RE.match(head):
        return
    level, level_name = _guess_level(head)
    detail = "\n".join(lines[1:]) or None
    if level < logging.ERROR and any(l.startswith("Traceback (most recent call last)") for l in lines):
        level, level_name = logging.ERROR, "ERROR"
    logs_sink.enqueue_record({
        "ts": datetime.now(timezone.utc),
        "level": level,
        "level_name": level_name,
        "source": "stderr",
        "logger_name": None,
        "message": head[:4096],
        "file": None,
        "line": None,
        "func": None,
        "exc_info": detail[:_GROUP_MAX_CHARS] if detail else None,
    })


def _read_loop() -> None:
    """Read lines from the pipe, tee to original stderr, group them into records, enqueue to sink."""
    assert _pipe_read_fd is not None and _orig_stderr_fd is not None
    grouper = _Grouper(_emit_group)
    buf = b""
    while True:
        try:
            chunk = os.read(_pipe_read_fd, 4096)
        except OSError:
            return
        if not chunk:
            return
        # Always preserve the terminal output.
        try:
            os.write(_orig_stderr_fd, chunk)
        except OSError:
            pass

        buf += chunk
        while b"\n" in buf:
            line_b, _, buf = buf.partition(b"\n")
            try:
                line = line_b.decode("utf-8", errors="replace").rstrip("\r")
            except Exception:
                continue
            if not line.strip():
                continue
            grouper.feed(line)


def install() -> None:
    """Idempotently redirect FD 2 through a pipe."""
    global _orig_stderr_fd, _pipe_read_fd, _pipe_write_fd, _reader_thread, _installed
    if _installed:
        return
    if os.getenv("FERNKAM_LOG_CAPTURE_STDERR", "1") == "0":
        return
    try:
        _orig_stderr_fd = os.dup(2)
        r, w = os.pipe()
        _pipe_read_fd, _pipe_write_fd = r, w
        # Replace process stderr (FD 2) with the pipe's write end.
        os.dup2(w, 2)
        # Make Python's sys.stderr line-buffered onto the new FD.
        try:
            sys.stderr.flush()
        except Exception:
            pass
        _reader_thread = threading.Thread(
            target=_read_loop, name="fernkam-stderr-capture", daemon=True
        )
        _reader_thread.start()
        _installed = True
    except Exception as e:
        logger.warning("[stderr_capture] install failed: %s", e)


def uninstall() -> None:
    """Restore original FD 2; reader thread exits when pipe is closed."""
    global _installed
    if not _installed:
        return
    try:
        if _orig_stderr_fd is not None:
            os.dup2(_orig_stderr_fd, 2)
        if _pipe_write_fd is not None:
            try:
                os.close(_pipe_write_fd)
            except OSError:
                pass
    except Exception as e:
        logger.warning("[stderr_capture] uninstall: %s", e)
    finally:
        _installed = False
