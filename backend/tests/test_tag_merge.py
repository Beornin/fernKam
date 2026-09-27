"""Check merging one tag tree into another.

Merging A into B moves A's photos onto B, merges A's children into B's
children of the same name (recursively) and moves the rest under B, keeping
approvals. Runs against the configured database inside a transaction that is
always rolled back, so nothing is kept.

Run directly: python backend/tests/test_tag_merge.py
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from fernkam.api.routers.tags import _merge
from fernkam.db.session import async_session_factory


async def main() -> None:
    root = f"ZZMergeTest_{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        try:
            p1, p2, p3 = (await db.execute(text("SELECT id FROM photos ORDER BY id LIMIT 3"))).scalars().all()

            async def tag(path: str, parent=None) -> int:
                return (await db.execute(text(
                    "INSERT INTO tags (name, path, parent_id, is_person) VALUES (:n, CAST(:p AS ltree), :par, false) RETURNING id"),
                    {"n": path.split(".")[-1], "p": f"{root}.{path}", "par": parent})).scalar_one()

            top = await tag("Top")                     # parent for both trees
            await db.execute(text("UPDATE tags SET path = CAST(:p AS ltree) WHERE id = :id"), {"p": root, "id": top})
            a = await tag("A", top); a_birds = await tag("A.Birds", a); a_hawk = await tag("A.Birds.Hawk", a_birds)
            a_owl = await tag("A.Birds.Owl", a_birds)
            b = await tag("B", top); b_birds = await tag("B.birds", b); b_hawk = await tag("B.birds.hawk", b_birds)
            link = text("INSERT INTO photo_tags (photo_id, tag_id, verified_at) VALUES (:p, :t, CASE WHEN :v THEN now() END)")
            await db.execute(link, {"p": p1, "t": a_hawk, "v": True})
            await db.execute(link, {"p": p1, "t": b_hawk, "v": False})   # same photo on both: stays once, approved
            await db.execute(link, {"p": p2, "t": a_hawk, "v": False})
            await db.execute(link, {"p": p3, "t": a_owl, "v": False})

            await _merge(db, a, b)

            rows = {r.path.removeprefix(root + "."): r for r in (await db.execute(text(
                "SELECT id, path::text AS path, parent_id FROM tags WHERE path <@ CAST(:r AS ltree)"), {"r": root})).all()}
            assert set(rows) == {root, "B", "B.birds", "B.birds.hawk", "B.birds.Owl"}, sorted(rows)
            assert rows["B.birds.Owl"].id == a_owl and rows["B.birds.Owl"].parent_id == b_birds
            hawk = {r.photo_id: r.verified for r in (await db.execute(text(
                "SELECT photo_id, verified_at IS NOT NULL AS verified FROM photo_tags WHERE tag_id = :t"), {"t": b_hawk})).all()}
            assert hawk == {p1: True, p2: False}, hawk
            assert (await db.execute(text("SELECT count(*) FROM photo_tags WHERE tag_id = :t"), {"t": a_owl})).scalar_one() == 1
        finally:
            await db.rollback()
    print("ok - merge moves photos and children, merges same-named children, keeps approvals")


if __name__ == "__main__":
    asyncio.run(main())
