"""auto_write: quick edits to one photo become one write, after the photo is left alone; a request
touching more than MAX_IDS photos is a bulk job left to DB → Files.

Run: uv run python tests/test_auto_write.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam import auto_write


async def scenario() -> None:
    written: list[int] = []

    async def fake_write(pid: int) -> None:
        written.append(pid)

    auto_write._write = fake_write
    auto_write.DELAY = 0.2

    auto_write.queue([1])
    await asyncio.sleep(0.1)
    auto_write.queue([1, 2])                   # edited again before its write: one write, later
    assert auto_write.is_queued(1) and not written
    await asyncio.sleep(0.15)
    assert not written, written                 # 1's clock restarted at the second edit
    await asyncio.sleep(0.2)
    assert sorted(written) == [1, 2], written
    assert not auto_write.is_queued(1)

    auto_write.queue(range(100, 100 + auto_write.MAX_IDS + 1))   # bulk: left to DB → Files
    assert not auto_write.is_queued(100)
    await asyncio.sleep(0.3)
    assert sorted(written) == [1, 2], written


def main() -> None:
    asyncio.run(scenario())
    print("ok - edits to a photo coalesce into one write; bulk requests stay batch")


if __name__ == "__main__":
    main()
