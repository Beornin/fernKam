"""A folder filter takes the folder and its subfolders, never a sibling whose name starts the same.

Run: uv run python tests/test_in_album.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.db.models.photos import Photo
from fernkam.services.photo_query import in_album


def sql(path: str) -> str:
    return str(in_album(Photo.album_path, path).compile(compile_kwargs={"literal_binds": True}))


def main() -> None:
    # SQLAlchemy escapes with "/", so "//" is a literal slash in the LIKE pattern.
    s = sql("/People/Sam")
    assert "= 'People/Sam'" in s, s
    assert "LIKE 'People//Sam//' || '%'" in s, s   # only "People/Sam/...": "Sam at Beach" stays out
    assert "'AA/_RAW//'" in sql("AA_RAW"), sql("AA_RAW")   # "_" is not a wildcard
    assert sql("/") == "true" and sql("") == "true", sql("/")   # the whole library
    print("ok - a folder filter takes the folder and its subfolders, not look-alike siblings")


if __name__ == "__main__":
    main()
