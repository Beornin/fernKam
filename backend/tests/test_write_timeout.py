"""A write-back batch that runs out of time leaves no exiftool temp file behind.

exiftool writes "<file>_exiftool_tmp" and swaps it in only when done; killed
mid-file, the temp copy stays and blocks every later write to that photo. The
batch must clear it (the original is untouched) and report the batch as not
written, so the next pass tries again. Also: the time allowed grows with size.

Run directly: python backend/tests/test_write_timeout.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fernkam.metadata_sync as ms


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        big = Path(td) / "scan.tif"
        big.write_bytes(b"\0" * 5_000_000)
        seen = {}

        def fake_run(args, **kw):
            seen["timeout"] = kw["timeout"]
            Path(str(big) + "_exiftool_tmp").write_bytes(b"\0" * 1000)   # killed mid-file
            raise subprocess.TimeoutExpired(args, kw["timeout"])

        real = ms.subprocess.run
        ms.subprocess.run = fake_run
        try:
            written, failed = ms.write_metadata_batch([{"SourceFile": str(big), "XMP-dc:Subject": ["x"]}])
        finally:
            ms.subprocess.run = real
        assert written == [] and str(big) in failed, (written, failed)
        assert not Path(str(big) + "_exiftool_tmp").exists(), "temp copy left behind"
        assert big.stat().st_size == 5_000_000, "original touched"
        assert seen["timeout"] >= 300
    print("ok - a timed-out write-back clears exiftool's temp copy and leaves the original")


if __name__ == "__main__":
    main()
