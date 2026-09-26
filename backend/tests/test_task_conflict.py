"""Check that two file-mutating tasks cannot run at once.

Two concurrent scans created 2,833 duplicate photo rows (migration 0026), and a
scan racing a file-move can delete rows for files that merely moved. The guard
lives inside create_task so a new endpoint cannot forget it — this asserts it is
still there, still narrow, and still lets the read-only work through.

Exercises the real TaskManager with the database stubbed out (its writes are
best-effort and swallow errors), so no live database is needed.

Run directly: python backend/tests/test_task_conflict.py
"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from fernkam.task_manager import FILE_MUTATING_TASKS, GPU_TASKS, Task, TaskConflict, TaskManager


class StubManager(TaskManager):
    """The real guard, with `tasks` rows simulated in memory."""

    def __init__(self, preexisting: "list[Task]" = ()):
        super().__init__()
        self._rows = list(preexisting)

    async def _db_session(self):
        raise RuntimeError("no database in this test")

    async def get_running_tasks(self):
        # Like a real query: the snapshot is taken, then the result comes back
        # after a round-trip during which other requests run.
        rows = [t for t in self._rows + list(self._cache.values()) if t.status == "running"]
        await asyncio.sleep(0.01)
        return rows


def running(task_type: str) -> Task:
    return Task(id=f"pre-{task_type}", task_type=task_type, status="running", message="")


async def expect_conflict(m: TaskManager, task_type: str, busy_type: str) -> None:
    try:
        await m.create_task(task_type, "")
    except TaskConflict as e:
        assert e.running.task_type == busy_type, e.running
        assert busy_type in str(e), str(e)
        return
    raise AssertionError(f"{task_type} was allowed while {busy_type} runs")


async def main() -> None:
    # The ones that touch files must exclude each other, in both directions.
    assert FILE_MUTATING_TASKS == {
        "scan_library", "workflow_sorting",
        "workflow_remove_nonkeep_raw", "workflow_move_raws", "workflow_pureraw",
        "workflow_finish_shoot",
    }, FILE_MUTATING_TASKS

    for busy in sorted(FILE_MUTATING_TASKS):
        for attempt in sorted(FILE_MUTATING_TASKS):
            await expect_conflict(StubManager([running(busy)]), attempt, busy)

    # Read-only work keeps running alongside — serialising it would mean no
    # searching during a 20-minute embed for no safety gain.
    m = StubManager([running("scan_library")])
    for ok in ("embed_photos", "auto_confirm", "cluster_rebuild", "geocode", "vacuum_analyze"):
        await m.create_task(ok, "")

    # GPU jobs take turns: two at once overflow VRAM and both crawl. PureRAW
    # is in both groups; a scan (not a GPU job) may run beside an index.
    for busy in sorted(GPU_TASKS):
        for attempt in sorted(GPU_TASKS):
            await expect_conflict(StubManager([running(busy)]), attempt, busy)
    await StubManager([running("model_install")]).create_task("scan_library", "")
    await expect_conflict(StubManager([running("model_install")]), "workflow_pureraw", "model_install")
    await expect_conflict(StubManager([running("workflow_pureraw")]), "workflow_remove_nonkeep_raw", "workflow_pureraw")

    # A finished scan must not block the next one.
    m = StubManager([Task(id="x", task_type="scan_library", status="completed", message="")])
    await m.create_task("scan_library", "")

    # Double-click: both requests are in flight before either has inserted its
    # row. The check-then-insert must be atomic, so exactly one wins.
    m = StubManager()
    results = await asyncio.gather(
        m.create_task("scan_library", ""), m.create_task("scan_library", ""),
        return_exceptions=True,
    )
    assert sum(isinstance(r, TaskConflict) for r in results) == 1, results

    # Cancel flips the status immediately, but the work may still be running
    # (a thread cannot be interrupted) — the guard must hold until the work
    # itself acknowledges.
    m = StubManager()
    tid = await m.create_task("workflow_move_raws", "")
    assert await m.cancel_task(tid)
    assert (await m.get_task(tid)).status == "cancelled"
    await expect_conflict(m, "scan_library", "workflow_move_raws")
    try:
        m.ensure_no_file_task()
        raise AssertionError("ensure_no_file_task passed while a file task was busy")
    except TaskConflict:
        pass
    await m.update_task(tid, status="cancelled", message="stopped")  # the work acknowledges
    await m.create_task("scan_library", "")

    # Work that runs to completion releases the guard too.
    m = StubManager()
    tid = await m.create_task("scan_library", "")
    await m.update_task(tid, status="completed", message="done")
    m.ensure_no_file_task()
    await m.create_task("workflow_sorting", "")

    print("ok - file-mutating tasks are mutually exclusive, read-only tasks are not")


if __name__ == "__main__":
    asyncio.run(main())
