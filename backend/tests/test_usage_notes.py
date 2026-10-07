"""File metadata edits only add: usage notes append, blank fields never clear.

A JPEG with a copyright and a caption gets two usage notes (with &, <, quotes,
an em dash and accents) and a credit line; the notes read back in order, the
credit is written, and a blank copyright in the edit leaves the old one, and
the caption, untouched. No database.

Run directly: python backend/tests/test_usage_notes.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from fernkam.api.routers.photos import read_file_fields, write_file_fields
from fernkam.metadata_sync import _get_et_session


def main() -> None:
    et = _get_et_session()
    assert et, "exiftool not found"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "a.jpg"
        Image.new("RGB", (8, 8), "white").save(f)
        assert "1 image files updated" in et.execute(
            ["-XMP-dc:Rights=(c) Someone", "-XMP-dc:Description=A crayfish", "-overwrite_original", str(f)])
        assert read_file_fields(et, f)["usage"] == []
        first = "2026-10-01 — Université X: web page <https://x.edu/a> & PDF \"Crayfish\""
        second = "2026-11-02 — Museum Y: exhibit print"
        assert write_file_fields(et, f, {"credit": "Photo: J. D.", "copyright": "  "}, first) is None
        assert write_file_fields(et, f, {}, second) is None
        got = read_file_fields(et, f)
        assert got["usage"] == [first, second], got["usage"]
        assert got["credit"] == "Photo: J. D." and got["copyright"] == "(c) Someone", got
        caption = json.loads(et.execute(["-j", "-XMP-dc:Description", str(f)]))[0]["Description"]
        assert caption == "A crayfish", caption
        assert write_file_fields(et, f, {"source": ""}) is None   # nothing to write: no call at all
    print("ok - file metadata: usage notes append in order; blank fields never clear; caption untouched")


if __name__ == "__main__":
    main()
