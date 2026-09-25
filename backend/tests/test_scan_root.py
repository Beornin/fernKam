"""Check that scans stay inside LIBRARY_ROOT.

Photos are catalogued by their path relative to LIBRARY_ROOT, so a folder
outside it produced rows pointing at files that do not exist.

Run directly: python backend/tests/test_scan_root.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "Library"
        (lib / "Trip" / "Day2").mkdir(parents=True)
        outside = Path(td) / "SDCard" / "DCIM"
        outside.mkdir(parents=True)

        os.environ["LIBRARY_ROOT"] = str(lib)
        from fernkam.config import get_settings
        get_settings.cache_clear()
        from fernkam.importers.filesystem import ScanRootError, resolve_scan_root

        assert resolve_scan_root(None) == lib
        assert resolve_scan_root("  ") == lib
        assert resolve_scan_root(str(lib / "Trip")) == lib / "Trip"
        assert resolve_scan_root("Trip/Day2") == lib / "Trip" / "Day2"          # relative to the library
        assert resolve_scan_root(str(lib / "Trip").replace("/", "\\")) == lib / "Trip"  # Windows-style slashes
        assert resolve_scan_root(str(lib)) == lib

        for bad in (str(outside), str(lib / ".." / "SDCard"), "../SDCard", str(Path(td))):
            try:
                resolve_scan_root(bad)
                raise AssertionError(f"{bad} was accepted")
            except ScanRootError as e:
                assert "outside the library" in str(e), e

    print("ok - scans stay inside LIBRARY_ROOT")


if __name__ == "__main__":
    main()
