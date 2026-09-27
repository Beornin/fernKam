"""Check which albums count as prep folders, where faces aren't detected.

Run directly: python backend/tests/test_prep_folders.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.config import Settings


def main() -> None:
    s = Settings(raw_intake_folder="AA_RAW", dedup_staging_folders="AB_TO_SORT, AC_SORTED")
    for album in ("AA_RAW", "AA_RAW/20260925_00029/jpg", "/AB_TO_SORT/SORT ME", "AC_SORTED/2026/09/x"):
        assert s.is_prep(album), album
    for album in ("", "Ordered by Dates/2026/09", "Portfolio/AA_RAW", "AA_RAW_old", "AC_SORTED2"):
        assert not s.is_prep(album), album
    print("ok - prep folders: AA_RAW, AB_TO_SORT, AC_SORTED and below only")


if __name__ == "__main__":
    main()
