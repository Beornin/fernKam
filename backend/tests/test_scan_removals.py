"""Check which catalogue rows a library scan is allowed to delete.

Deleting a photo row cascades to its tags, faces and rating, so "not seen on
this walk" must not be treated as "gone" when the walk could not see it.

Run directly: python backend/tests/test_scan_removals.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.importers.filesystem import _select_removed

LIB = Path("/lib")


def main() -> None:
    existing = {
        ("/", "root.jpg"): (1, None),
        ("Trip", "a.jpg"): (2, None),
        ("Trip", "b.jpg"): (3, None),
        ("Trip/Day2", "c.jpg"): (4, None),
        ("Other", "d.jpg"): (5, None),
    }
    everything = set(existing)

    # Nothing missing -> nothing removed.
    removed, reason = _select_removed(existing, everything, LIB, LIB, [])
    assert removed == [] and reason is None, (removed, reason)

    # A file really gone is removed, with its path so the DELETE can check the
    # row was not moved meanwhile.
    removed, reason = _select_removed(existing, everything - {("Trip", "b.jpg")}, LIB, LIB, [])
    assert removed == [(3, "Trip", "b.jpg")] and reason is None, (removed, reason)

    # A folder os.walk could not list: its rows (and its subfolders') are kept,
    # while a real deletion elsewhere still goes through.
    seen = {("/", "root.jpg"), ("Other", "d.jpg")}
    removed, reason = _select_removed(existing, seen, LIB, LIB, [LIB / "Trip"])
    assert removed == [] and reason is None, (removed, reason)
    removed, _ = _select_removed(existing, seen - {("Other", "d.jpg")}, LIB, LIB, [LIB / "Trip"])
    assert removed == [(5, "Other", "d.jpg")], removed

    # Unplugged drive / empty LIBRARY_ROOT: the walk saw no media at all.
    removed, reason = _select_removed(existing, set(), LIB, LIB, [])
    assert removed == [] and reason and "5 photos" in reason, (removed, reason)

    # A scan of one subfolder only considers rows under it.
    removed, _ = _select_removed(existing, {("Trip", "a.jpg")}, LIB, LIB / "Trip", [])
    assert sorted(removed) == [(3, "Trip", "b.jpg"), (4, "Trip/Day2", "c.jpg")], removed

    # ...and an empty subfolder scan deletes nothing even though rows exist there.
    removed, reason = _select_removed(existing, set(), LIB, LIB / "Trip", [])
    assert removed == [] and reason and "3 photos" in reason, (removed, reason)

    # A custom path outside the library never touches library rows.
    removed, reason = _select_removed(existing, {("DCIM", "x.jpg")}, LIB, Path("/media/sd"), [])
    assert removed == [] and reason is None, (removed, reason)

    print("ok - scan removals keep rows it could not see")


if __name__ == "__main__":
    main()
