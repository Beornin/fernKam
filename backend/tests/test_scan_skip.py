"""Check that scan_library's walk skips files whose mtime is unchanged.

Run directly: python backend/tests/test_scan_skip.py
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.importers.filesystem import MTIME_SLACK_SECONDS, _stat_walk_batch


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name in ("unchanged.jpg", "touched.jpg", "brandnew.jpg", "notes.txt"):
            (root / name).write_bytes(b"x")

        def mtime_of(name: str) -> datetime:
            return datetime.fromtimestamp((root / name).stat().st_mtime, tz=timezone.utc)

        existing = {
            ("/", "unchanged.jpg"): (101, mtime_of("unchanged.jpg")),
            # synced an hour before the file's current mtime -> must refresh
            ("/", "touched.jpg"): (102, mtime_of("touched.jpg") - timedelta(hours=1)),
        }
        files = ["unchanged.jpg", "touched.jpg", "brandnew.jpg", "notes.txt"]
        new, upd, keys, skipped = _stat_walk_batch(str(root), files, root, root, existing)

        assert skipped == 1, f"expected 1 unchanged file skipped, got {skipped}"
        assert [f[2] for f in upd] == ["touched.jpg"], f"expected only touched.jpg refreshed, got {upd}"
        assert [f[2] for f in new] == ["brandnew.jpg"], f"expected only brandnew.jpg new, got {new}"
        # notes.txt is not a media extension; the other three must all be tracked
        # so deleted-file detection still sees them on disk.
        assert keys == {("/", "unchanged.jpg"), ("/", "touched.jpg"), ("/", "brandnew.jpg")}, keys

        # Sub-slack drift (FAT rounding, network shares) must not force a re-read.
        jitter = {("/", "unchanged.jpg"): (
            101, mtime_of("unchanged.jpg") + timedelta(seconds=MTIME_SLACK_SECONDS / 2))}
        _, upd2, _, skipped2 = _stat_walk_batch(str(root), ["unchanged.jpg"], root, root, jitter)
        assert skipped2 == 1 and not upd2, f"sub-slack jitter should skip, got {skipped2}/{upd2}"

        # A file restored with an OLDER timestamp is still a change.
        rewound = {("/", "unchanged.jpg"): (101, mtime_of("unchanged.jpg") + timedelta(days=3))}
        _, upd3, _, skipped3 = _stat_walk_batch(str(root), ["unchanged.jpg"], root, root, rewound)
        assert skipped3 == 0 and len(upd3) == 1, f"rewound mtime must refresh, got {skipped3}/{upd3}"

        # A row that never recorded a sync mtime must refresh, not be skipped.
        never = {("/", "unchanged.jpg"): (101, None)}
        _, upd4, _, skipped4 = _stat_walk_batch(str(root), ["unchanged.jpg"], root, root, never)
        assert skipped4 == 0 and len(upd4) == 1, f"NULL sync mtime must refresh, got {skipped4}/{upd4}"

    print("ok - scan skip logic")


if __name__ == "__main__":
    main()
