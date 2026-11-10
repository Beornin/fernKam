"""stderr capture: a traceback is one log record, not one row per line, and what the Python log
handler already stores is not stored twice.

Run: uv run python tests/test_stderr_group.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam import logs_sink, stderr_capture


def main() -> None:
    got = []
    logs_sink.enqueue_record = got.append
    g = stderr_capture._Grouper(stderr_capture._emit_group)
    lines = [
        # a native library warning: one record
        "[ WARN:0@227.9] global loadsave.cpp (239) findDecoder imread_('x.png'): can't open/read file",
        # a traceback printed by something that isn't a fernkam logger: ONE record
        "Traceback (most recent call last):",
        '  File "app.py", line 12, in handler',
        "    run()",
        "ValueError: boom",
        # the terminal copy of what the Python handler already stored (with its traceback): none
        "ERROR [fernkam.error] Unhandled exception: (sqlalchemy.dialects.postgresql.asyncpg.InterfaceError) too many",
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "sqlalchemy.exc.InterfaceError: too many arguments",
        "(Background on this error at: https://sqlalche.me/e/20/rvf5)",
        "WARNI [fernkam.metadata_sync] exiftool batch write timed out (31 files, 2.1 GB)",
        # a plain INFO line after that: its own record
        "INFO:     127.0.0.1:5000 - GET /api/health",
    ]
    for line in lines:
        g.feed(line)
    g.flush()
    msgs = [(r["level_name"], r["message"][:40], (r["exc_info"] or "").count("\n") + 1 if r["exc_info"] else 0) for r in got]
    assert len(got) == 3, msgs
    assert got[0]["source"] == "stderr" and "imread" in got[0]["message"] and got[0]["exc_info"] is None
    assert got[1]["message"] == "Traceback (most recent call last):" and got[1]["level"] >= 40       # a traceback is an error
    assert "ValueError: boom" in got[1]["exc_info"] and 'File "app.py"' in got[1]["exc_info"]
    assert got[2]["message"].startswith("INFO:")
    print("ok - a traceback is one record; what the Python handler stored is not repeated")


if __name__ == "__main__":
    main()
