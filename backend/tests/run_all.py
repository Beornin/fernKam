"""Run every test file, each in its own process, and report what passed.

    uv run python tests/run_all.py            # all
    uv run python tests/run_all.py cull sort  # only files whose name contains one of these

No pytest needed. A file with a main() (the script-style tests) runs as a script; a file of plain
test_* functions (the pytest-style ones) has each argument-free function called, async ones awaited.
A test that needs a pytest fixture is skipped and listed. Exit status is non-zero if any file fails.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SRC = TESTS.parent / "src"

HARNESS = r"""
import asyncio, importlib.util, inspect, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
spec = importlib.util.spec_from_file_location("under_test", sys.argv[3])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ran = skipped = 0
for name, fn in list(vars(mod).items()):
    if not (name.startswith("test_") and callable(fn)) or getattr(fn, "__module__", None) != mod.__name__:
        continue
    if inspect.signature(fn).parameters:
        skipped += 1
        continue
    r = fn()
    if inspect.iscoroutine(r):
        asyncio.run(r)
    ran += 1
print(f"{ran} test functions" + (f", {skipped} skipped (need a fixture)" if skipped else ""))
"""


def run(path: Path) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    # A file with a main guard, or with no test_ functions at all (it checks things as it runs),
    # is a script; a file of test_ functions is called function by function.
    script = "__main__" in text or not re.search(r"^(?:async )?def test_", text, re.M)
    cmd = [sys.executable, str(path)] if script else [sys.executable, "-c", HARNESS, str(SRC), str(TESTS), str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    last = (r.stdout.strip().splitlines() or [""])[-1] or (r.stderr.strip().splitlines() or [""])[-1]
    return r.returncode == 0, last[:150]


def main() -> int:
    wanted = sys.argv[1:]
    files = sorted(p for p in TESTS.glob("test_*.py") if not wanted or any(w in p.name for w in wanted))
    failed = []
    for p in files:
        ok, last = run(p)
        print(f"{'ok  ' if ok else 'FAIL'}  {p.name:38} {last}")
        if not ok:
            failed.append(p.name)
    print(f"\n{len(files) - len(failed)} of {len(files)} files passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
