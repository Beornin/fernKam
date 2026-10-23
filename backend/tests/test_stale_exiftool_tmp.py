"""A write cut off mid-file leaves "<file>_exiftool_tmp", and exiftool then refuses that file for
good: the write-back removes an old one beside an intact original, never a fresh one.

Run: uv run python tests/test_stale_exiftool_tmp.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.metadata_sync import _clear_stale_tmp


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        old, fresh, orphan = (Path(d) / n for n in ("old.tif", "fresh.tif", "orphan.tif"))
        for f in (old, fresh):
            f.write_bytes(b"x")
        for f in (old, fresh, orphan):
            Path(f"{f}_exiftool_tmp").write_bytes(b"partial")
        hour_ago = time.time() - 3600
        for f in (old, orphan):
            os.utime(f"{f}_exiftool_tmp", (hour_ago, hour_ago))
        _clear_stale_tmp([str(old), str(fresh), str(orphan)])
        assert not Path(f"{old}_exiftool_tmp").exists()      # stale, original intact: removed
        assert Path(f"{fresh}_exiftool_tmp").exists()        # could be a write in progress
        assert Path(f"{orphan}_exiftool_tmp").exists()       # no original: it may be all that's left
    print("ok - stale exiftool temp files are cleared, fresh or orphaned ones kept")


if __name__ == "__main__":
    main()
