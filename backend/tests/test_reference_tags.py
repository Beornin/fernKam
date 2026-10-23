"""Ref tags: the archive's prefix by its top-level folder (ARCHIVE_FOLDER), and the Ref number.

Run: uv run python tests/test_reference_tags.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.update(ARCHIVE_FOLDER="Archive", REF_PREFIX="REF", ARCHIVE_REF_PREFIX="ARC")   # over whatever .env says

from fernkam.config import get_settings
from fernkam.reference_tags import _NUM, in_archive, number, prefix


def main() -> None:
    get_settings.cache_clear()
    assert prefix("Archive/Level 2/Crayfish/x") == "ARC" and in_archive("Archive")
    assert prefix("Portfolio/Birds/Osprey (Pandion haliaetus)") == "REF"
    assert prefix("ArchiveOld/x") == "REF"           # only the archive folder itself
    os.environ["ARCHIVE_FOLDER"] = ""
    get_settings.cache_clear()
    assert not in_archive("Archive/x") and not in_archive("")   # no archive set: all the user's
    assert number(272036, {5, 9}) == 272036         # the photo's own id when free
    assert number(9, {5, 9}) == 10                  # taken (rebuilt database): the next free one
    assert _NUM.match("REF-272036")[1] == "272036" and not _NUM.match("Reference")
    print("ok - archive prefix by its folder; Ref numbers")


if __name__ == "__main__":
    main()
