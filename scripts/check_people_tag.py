"""Check if People tag still exists after face removal."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))

from fernkam.db.session import async_session_factory
from fernkam.db.models.photos import Tag
from sqlalchemy import select, text, func


async def main():
    async with async_session_factory() as db:
        people = (await db.execute(
            select(Tag).where(Tag.name.ilike("people"))
        )).scalar_one_or_none()
        
        print(f"People tag exists: {people is not None}")
        if people:
            print(f"People tag ID: {people.id}")
            print(f"People tag path: {people.path}")
            print(f"People tag is_person: {people.is_person}")
            
            # Count children
            children = (await db.execute(
                select(func.count(Tag.id)).where(text(f"path <@ '{people.path}'"))
            )).scalar_one()
            print(f"Children count: {children}")
            
            # List children
            child_result = await db.execute(
                select(Tag).where(text(f"path <@ '{people.path}'"))
            )
            children_list = child_result.scalars().all()
            print(f"Children: {[c.name for c in children_list]}")
        else:
            print("People tag not found - checking for any person tags...")
            person_tags = (await db.execute(
                select(Tag).where(Tag.is_person == True)
            )).scalars().all()
            print(f"Person tags remaining: {len(person_tags)}")
            for pt in person_tags:
                print(f"  - {pt.name} (path: {pt.path})")


if __name__ == "__main__":
    asyncio.run(main())
