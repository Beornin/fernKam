"""Check tags table for person tags and DigiKam person structure."""
import asyncio
from sqlalchemy import select, func
from fernkam.db.session import async_session_factory
from fernkam.db.models.photos import Tag, Face

async def main():
    async with async_session_factory() as db:
        # Total tags
        total_tags = (await db.execute(select(func.count(Tag.id)))).scalar_one()
        print(f"Total tags: {total_tags}")

        # Person tags (is_person=True)
        person_tags = (await db.execute(
            select(func.count(Tag.id)).where(Tag.is_person == True)
        )).scalar_one()
        print(f"Person tags (is_person=True): {person_tags}")

        # Tags with faces assigned
        tags_with_faces = (await db.execute(
            select(func.count(Tag.id.distinct()))
            .join(Face, Face.person_tag_id == Tag.id)
        )).scalar_one()
        print(f"Tags with faces assigned: {tags_with_faces}")

        # Show top tags by face count
        top_tags = (await db.execute(
            select(Tag.id, Tag.name, Tag.path, Tag.is_person, func.count(Face.id).label("fc"))
            .outerjoin(Face, Face.person_tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(func.count(Face.id).desc())
            .limit(20)
        )).fetchall()

        print("\nTop 20 tags by face count:")
        for r in top_tags:
            print(f"  {r.id:5d} | {r.name:30s} | is_person={r.is_person} | faces={r.fc}")

        # Check for DigiKam People tag hierarchy
        people_root = (await db.execute(
            select(Tag).where(Tag.name.ilike("%people%"))
        )).fetchall()
        print(f"\nTags with 'People' in name: {len(people_root)}")
        for t in people_root:
            print(f"  {t.id} | {t.name} | {t.path}")

if __name__ == "__main__":
    asyncio.run(main())
