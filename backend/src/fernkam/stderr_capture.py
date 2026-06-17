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


def _read_loop() -> None:
    """Read lines from the pipe, tee to original stderr, enqueue to sink."""
    from fernkam import logs_sink  # local import to avoid cycle on early import

    assert _pipe_read_fd is not None and _orig_stderr_fd is not None
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
            if _STDERR_BLOCKLIST.search(line):
                continue
            level, level_name = _guess_level(line)
            # Only forward if it looks meaningful (skip our own debug noise).
            try:
                logs_sink.enqueue_record({
                    "ts": datetime.now(timezone.utc),
                    "level": level,
                    "level_name": level_name,
                    "source": "stderr",
                    "logger_name": None,
                    "message": line[:4096],
                    "file": None,
                    "line": None,
                    "func": None,
                    "exc_info": None,
                })
            except Exception:
                pass


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
