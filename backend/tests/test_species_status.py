"""Check Status fact tags follow the species tags they come from.

A photo with a species that is Critically Endangered and introduced gets both
Status tags; Least Concern gets none; taking the species off the photo takes
the Status tags off too. Runs inside a transaction that is rolled back.

Run directly: python backend/tests/test_species_status.py
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from fernkam import species_status as ss
from fernkam.db.session import async_session_factory


async def main() -> None:
    assert ss.status_names("CR", True) == ["IUCN Critically Endangered", "Introduced to the US"]
    assert ss.status_names("LC", False) == [] and ss.status_names(None, False) == []
    label = f"ZZStatusTest_{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        try:
            p1, p2 = (await db.execute(text("SELECT id FROM photos ORDER BY id LIMIT 2"))).scalars().all()
            wild = (await db.execute(text("SELECT id FROM tags WHERE path = 'Wildlife'"))).scalar_one()

            async def species(name: str, iucn: str, intro: bool) -> int:
                tid = (await db.execute(text("""INSERT INTO tags (name, path, parent_id, is_person)
                    VALUES (:n, CAST(:p AS ltree), :w, false) RETURNING id"""),
                    {"n": name, "p": f"Wildlife.{label}_{iucn}", "w": wild})).scalar_one()
                await db.execute(text("INSERT INTO species_status (tag_id, iucn, introduced_us) VALUES (:t, :i, :u)"),
                                 {"t": tid, "i": iucn, "u": intro})
                return tid
            rare = await species("Rare (Rarus testus)", "CR", True)
            common = await species("Common (Communis testus)", "LC", False)
            await db.execute(text("INSERT INTO photo_tags (photo_id, tag_id) VALUES (:p, :t)"), {"p": p1, "t": rare})
            await db.execute(text("INSERT INTO photo_tags (photo_id, tag_id) VALUES (:p, :t)"), {"p": p2, "t": common})

            async def status_of(pid: int) -> set[str]:
                return set((await db.execute(text("""SELECT t.name FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                    WHERE pt.photo_id = :p AND t.path <@ 'Status'"""), {"p": pid})).scalars().all())

            before1, before2 = await status_of(p1), await status_of(p2)
            await ss.apply(db)
            assert await status_of(p1) - before1 == {"IUCN Critically Endangered", "Introduced to the US"}, await status_of(p1)
            assert await status_of(p2) == before2
            assert (await db.execute(text("SELECT is_fact FROM tags WHERE path = 'Status'"))).scalar_one()

            await db.execute(text("DELETE FROM photo_tags WHERE photo_id = :p AND tag_id = :t"), {"p": p1, "t": rare})
            await ss.apply(db)
            assert await status_of(p1) == before1 - {"IUCN Critically Endangered", "Introduced to the US"}, await status_of(p1)

            # The lookup links the tag for its range prior; a hand-picked link is kept.
            m = {"rank": "SPECIES", "classKey": 212, "class": "Aves", "canonicalName": "Rarus testus"}
            await ss.link(db, rare, 111, m)
            await ss.link(db, rare, 222, m)
            await ss.link(db, common, 212, {**m, "rank": "CLASS"})
            got = (await db.execute(text("SELECT tag_id, taxon_key, common_name FROM tag_species WHERE tag_id IN (:a, :b)"),
                                    {"a": rare, "b": common})).all()
            assert [tuple(r) for r in got] == [(rare, 111, "Rare")], got
            # Ray-finned fish have no class in GBIF: their order corrects for effort.
            await ss.link(db, common, 333, {"rank": "SPECIES", "orderKey": 587, "order": "Centrarchiformes", "canonicalName": "Communis testus"})
            got = (await db.execute(text("SELECT class_key, class_name FROM tag_species WHERE tag_id = :b"), {"b": common})).one()
            assert tuple(got) == (587, "Centrarchiformes"), got

            # Renaming keeps the lookup unless the Latin name changes (then GBIF looks again).
            from fernkam.api.routers.tags import update_tag
            db.commit = db.flush   # stay inside the rolled-back transaction
            looked = lambda: db.execute(text("""SELECT (SELECT count(*) FROM species_status WHERE tag_id = :t)
                + (SELECT count(*) FROM tag_species WHERE tag_id = :t)"""), {"t": rare})
            await update_tag(rare, db, name="Very Rare (Rarus testus)", parent_id=None, is_fact=None)
            assert (await looked()).scalar() == 2
            await update_tag(rare, db, name="Very Rare (Rarus other)", parent_id=None, is_fact=None)
            assert (await looked()).scalar() == 0
        finally:
            await db.rollback()
    print("ok - Status tags follow their species: added, and removed with it; the tag is linked once")


if __name__ == "__main__":
    asyncio.run(main())
