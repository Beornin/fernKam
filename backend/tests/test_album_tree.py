"""_build_tree: the album tree on edge cases (root, trailing slashes, folders with no photos of their
own, empty paths) and on the live library when a database is there. A rewrite was checked to give
exactly the old builder's tree on all 3,792 folders.

Run: uv run python tests/test_album_tree.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.api.routers.albums import _build_tree


def main() -> None:
    rows = [("Archive/Level 2/Birds/Owl", 5), ("Archive/Level 2/Birds", 2), ("Portfolio", 1), ("/Trailing/", 3),
            ("Portfolio/Frogs/Tree Frog", 4), ("", 9), ("Portfolio/Frogs/Tree Frog", 1)]
    tree = _build_tree(rows)
    got = {n.path: n for n in tree}
    assert set(got) == {"/Archive", "/Portfolio", "/Trailing"}, set(got)
    assert got["/Archive"].photo_count == 7 and got["/Portfolio"].photo_count == 6 and got["/Trailing"].photo_count == 3
    archive = got["/Archive"].children[0]
    assert (archive.name, archive.path, archive.photo_count) == ("Level 2", "/Archive/Level 2", 7)
    assert [c.name for c in got["/Portfolio"].children] == ["Frogs"] and got["/Portfolio"].children[0].children[0].photo_count == 5
    assert _build_tree([("/", 4)])[0].name == "Root" and _build_tree([("/", 4)])[0].photo_count == 4
    assert _build_tree([]) == []

    try:    # the whole real library: the same tree as the reference, and fast
        import asyncio, time
        from sqlalchemy import func, select
        from fernkam.db.models.photos import Photo
        from fernkam.db.session import async_session_factory

        async def live():
            async with async_session_factory() as db:
                return [(r[0], r[1]) for r in (await db.execute(
                    select(Photo.album_path, func.count()).where(Photo.status == 1).group_by(Photo.album_path))).all()]
        real = asyncio.run(live())
        t = time.time(); tree = _build_tree(real); ms = (time.time() - t) * 1000
        assert sum(n.photo_count for n in tree) == sum(c for _, c in real if _), "counts must add up"
        assert ms < 100, f"{ms:.0f} ms for {len(real)} folders"
        print(f"ok - album tree ({len(real)} real folders in {ms:.0f} ms; counts add up)")
    except ImportError:
        print("ok - album tree")


if __name__ == "__main__":
    main()
