"""Check that fernKam's exiftool session dies with fernKam.

The launcher stops the backend with taskkill /F, which runs no cleanup, and
each session's `exiftool -stay_open` process was left running: 5 after three
days. The session is now started inside a Windows job that kills it when its
parent ends. This kills a parent the same hard way and checks the child is
gone, next to a control without the job, where the child survives. Windows
only.

Run directly: python backend/tests/test_exiftool_tether.py
"""
import subprocess
import sys
import time
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")

PARENT = r"""
import subprocess, sys, time
sys.path.insert(0, {src!r})
from fernkam.metadata_sync import _die_with_this_process
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
if {tether}:
    _die_with_this_process(child)
print(child.pid, flush=True)
time.sleep(120)
"""


def alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def orphan_after_hard_kill(tether: bool) -> tuple[int, bool]:
    parent = subprocess.Popen([sys.executable, "-c", PARENT.format(src=SRC, tether=tether)],
                              stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    assert alive(child)
    parent.kill()              # TerminateProcess: no cleanup, like taskkill /F
    parent.wait()
    for _ in range(30):
        if not alive(child):
            break
        time.sleep(0.1)
    return child, alive(child)


def main() -> None:
    if sys.platform != "win32":
        print("skip - Windows only")
        return
    pid, survived = orphan_after_hard_kill(tether=False)
    assert survived, "control: an untethered child should outlive its killed parent"
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

    _, survived = orphan_after_hard_kill(tether=True)
    assert not survived, "tethered child outlived its killed parent"
    print("ok - exiftool's session dies with the backend, even on a hard kill (control child survives without it)")


if __name__ == "__main__":
    main()
