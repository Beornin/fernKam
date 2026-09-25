"""Check that files moved or renamed outside fernKam are matched to their row.

digiKam keeps a moved file's tags by matching its content hash; fernKam used
to delete the row (tags, faces, rating, embedding) and re-import the file bare.

Runs _match_moves against a stubbed database session — no live database.

Run directly: python backend/tests/test_move_match.py
"""
import asyncio
import hashlib
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.importers.filesystem import _match_moves


class Row:
    def __init__(self, id, sha256):
        self.id, self.sha256 = id, sha256


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        rows = self.rows

        class Result:
            def all(self):
                return rows
        return Result()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        now = datetime.now(timezone.utc)
        (root / "renamed.jpg").write_bytes(b"photo-one")
        (root / "copy_of_two.jpg").write_bytes(b"photo-two")
        (root / "brand_new.jpg").write_bytes(b"never seen")
        new_files = [(root / n, "Trip", n, now) for n in ("renamed.jpg", "copy_of_two.jpg", "brand_new.jpg")]
        # row 1's file vanished and reappears renamed; row 2 vanished too, its
        # content now exists once; row 3 vanished for real (no content match).
        removed = [(1, "Trip", "one.jpg"), (2, "Old", "two.jpg"), (3, "Old", "gone.jpg")]
        db = FakeDB([Row(1, sha(b"photo-one")), Row(2, sha(b"photo-two")), Row(3, sha(b"deleted"))])

        loop = asyncio.get_running_loop()
        moves, still_removed, still_new, hashes = await _match_moves(db, removed, new_files, loop, asyncio.Semaphore(4))

        assert [(m[0], m[2][2]) for m in moves] == [(1, "renamed.jpg"), (2, "copy_of_two.jpg")], moves
        assert still_removed == [(3, "Old", "gone.jpg")], still_removed
        assert [n[2] for n in still_new] == ["brand_new.jpg"], still_new
        # every new file was hashed once, for the import to reuse
        assert set(hashes) == {p for p, *_ in new_files}, hashes

        # nothing vanished -> nothing hashed, nothing matched
        assert await _match_moves(db, [], new_files, loop, asyncio.Semaphore(4)) == ([], [], new_files, {})

    print("ok - moved/renamed files keep their row")


if __name__ == "__main__":
    asyncio.run(main())
