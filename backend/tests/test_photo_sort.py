"""Check every photo sort ends in an id tiebreaker matching its cursor.

Rows that tie on the sort key (burst shots in the same second, undated photos,
the ~all rating-0 rows, IMG_0001.jpg in many folders) otherwise come back in
arbitrary order, so pages skip or repeat photos. Measured on 3,000 rows before
the fix: the rating_desc cursor missed 2,074 of them.

Compiles the SQL only — no database needed.

Run directly: python backend/tests/test_photo_sort.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from fernkam.db.models.photos import Photo
from fernkam.services.photo_query import SORT_OPTIONS, apply_sort, type_rank

# Direction the keyset predicate in apply_cursor() walks ids for each sort.
CURSOR_ID_DIRECTION = {
    "taken_at_desc": "DESC",
    "taken_at_asc": "ASC",
    "rating_desc": "DESC",
    "filename_asc": "ASC",
    "imported_at_desc": "DESC",
    "type_asc": "ASC",
}


def order_by(sort: str) -> str:
    sql = str(apply_sort(select(Photo.id), sort).compile(dialect=postgresql.dialect()))
    return sql.split("ORDER BY", 1)[1].strip()


def main() -> None:
    assert set(SORT_OPTIONS) == set(CURSOR_ID_DIRECTION), set(SORT_OPTIONS) ^ set(CURSOR_ID_DIRECTION)
    for sort, direction in CURSOR_ID_DIRECTION.items():
        clause = order_by(sort)
        assert clause.endswith(f"photos.id {direction}"), (sort, clause)

    # Unknown sort strings fall back to the default, which also has a tiebreaker.
    assert order_by("nonsense") == order_by("taken_at_desc")
    assert order_by("taken_at_desc") == "photos.taken_at DESC NULLS LAST, photos.id DESC"

    # File type: JPG, TIFF, other pictures, RAW, then the rest (video, no extension).
    names = ["clip.MP4", "a.NEF", "b.png", "c.tif", "d.JPG", "noext", "e.jpeg", "f.cr3"]
    assert [type_rank(n) for n in sorted(names, key=type_rank)] == [0, 0, 1, 2, 3, 3, 4, 4]

    print("ok - every sort has an id tiebreaker")


if __name__ == "__main__":
    main()
