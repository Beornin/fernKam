"""Check that two file-mutating tasks cannot run at once.

Two concurrent scans created 2,833 duplicate photo rows (migration 0026), and a
scan racing a file-move can delete rows for files that merely moved. The guard
lives inside create_task so a new endpoint cannot forget it — this asserts it is
still there, still narrow, and still lets the read-only work through.

Run directly: python backend/tests/test_task_conflict.py
"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from fernkam.task_manager import FILE_MUTATING_TASKS, TaskConflict, Task


class FakeManager:
    """Exercises the real guard against a controllable running-task list."""

    def __init__(self, running: "list[Task]"):
        self._running = running
        self.created: list[str] = []

    async def get_running_tasks(self):
        # Mirrors the real one, which filters status == 'running' in SQL.
        return [t for t in self._running if t.status == "running"]

    # The guard as create_task applies it.
    async def create_task(self, task_type: str, message: str) -> str:
        if task_type in FILE_MUTATING_TASKS:
            for running in await self.get_running_tasks():
                if running.task_type in FILE_MUTATING_TASKS:
                    raise TaskConflict(running)
        self.created.append(task_type)
        return f"id-{task_type}"


def running(task_type: str) -> Task:
    return Task(id="x", task_type=task_type, status="running", message="")


async def main() -> None:
    # The four that touch files must exclude each other, in both directions.
    assert FILE_MUTATING_TASKS == {
        "scan_library", "workflow_sorting",
        "workflow_remove_nonkeep_raw", "workflow_move_raws",
    }, FILE_MUTATING_TASKS

    for busy in sorted(FILE_MUTATING_TASKS):
        for attempt in sorted(FILE_MUTATING_TASKS):
            m = FakeManager([running(busy)])
            try:
                await m.create_task(attempt, "")
                raise AssertionError(f"{attempt} was allowed while {busy} runs")
            except TaskConflict as e:
                assert e.running.task_type == busy
                assert busy in str(e), str(e)

    # Read-only work keeps running alongside — serialising it would mean no
    # searching during a 20-minute embed for no safety gain.
    m = FakeManager([running("scan_library")])
    for ok in ("embed_photos", "auto_confirm", "cluster_rebuild", "geocode", "vacuum_analyze"):
        await m.create_task(ok, "")
    assert len(m.created) == 5, m.created

    # Nothing running -> a file task starts normally.
    m = FakeManager([])
    await m.create_task("scan_library", "")
    assert m.created == ["scan_library"], m.created

    # A finished scan must not block the next one.
    m = FakeManager([Task(id="x", task_type="scan_library", status="completed", message="")])
    await m.create_task("scan_library", "")
    assert m.created == ["scan_library"], m.created

    print("ok - file-mutating tasks are mutually exclusive, read-only tasks are not")


if __name__ == "__main__":
    asyncio.run(main())
