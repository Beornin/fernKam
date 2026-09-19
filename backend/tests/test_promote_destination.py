"""Check that promoting a photo preserves the RAW/ subfolder convention.

Run directly: python backend/tests/test_promote_destination.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.api.routers.workflows import promote_destination


def main() -> None:
    # A plain picture lands in the destination root.
    album, rel = promote_destination("Ordered by Dates/2024", "Portfolio/Snakes", "a.jpg")
    assert album == "Portfolio/Snakes", album
    assert rel == "Portfolio/Snakes/a.jpg", rel

    # A RAW carried from a RAW/ subfolder must land in one, or the pairing
    # that move_raws_to_folders and the stack rebuild rely on is broken.
    album, rel = promote_destination("Ordered by Dates/2024/RAW", "Portfolio/Snakes", "a.NEF")
    assert album == "Portfolio/Snakes/RAW", album
    assert rel == "Portfolio/Snakes/RAW/a.NEF", rel

    # Case-insensitive and tolerant of a trailing slash — album_path is written
    # by several different code paths.
    for src in ("X/raw", "X/Raw/", "X/RAW"):
        album, _ = promote_destination(src, "Portfolio/Birds", "b.NEF")
        assert album == "Portfolio/Birds/RAW", (src, album)

    # A folder merely *named* like a raw thing is not a RAW subfolder.
    album, _ = promote_destination("Portfolio/RAWkus Gallery", "Portfolio/Birds", "c.jpg")
    assert album == "Portfolio/Birds", album

    # Promoting within the same place is detectable as a no-op by the caller.
    album, _ = promote_destination("Portfolio/Snakes", "Portfolio/Snakes", "d.jpg")
    assert album == "Portfolio/Snakes", album

    print("ok - promote destination preserves RAW/ convention")


if __name__ == "__main__":
    main()
