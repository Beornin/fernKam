"""Check the live catalogue has no duplicate (album_path, filename) rows.

The scanner keys `existing_photos` by (album_path, filename) in a dict, so a
duplicate row collapses and becomes invisible to every phase: never matched
against disk, never refreshed, never deleted. It just accumulates, and the
symptom is a photo that will not open no matter how often you rescan.

Migration 0026 removed 2,833 of them and added a unique index. This asserts the
index is still there and still doing its job.

Run directly: python backend/tests/test_no_duplicate_photos.py
"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))


def _pg_url() -> str:
    env = {}
    for line in (BACKEND / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env["PG_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main() -> None:
    import asyncpg

    c = await asyncpg.connect(_pg_url())
    try:
        dupes = await c.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT album_path, filename FROM photos
                GROUP BY 1, 2 HAVING COUNT(*) > 1
            ) x
        """)
        assert dupes == 0, f"{dupes} duplicated (album_path, filename) groups"

        has_index = await c.fetchval(
            "SELECT to_regclass('uq_photos_album_filename') IS NOT NULL"
        )
        assert has_index, "uq_photos_album_filename is missing — duplicates can return"

        # The index must actually reject, not merely exist.
        row = await c.fetchrow("SELECT album_path, filename FROM photos LIMIT 1")
        if row is not None:
            tr = c.transaction()
            await tr.start()
            try:
                await c.execute(
                    "INSERT INTO photos (album_path, filename, status, media_type)"
                    " VALUES ($1, $2, 1, 'image')",
                    row["album_path"], row["filename"],
                )
                raise AssertionError("duplicate insert was accepted")
            except asyncpg.exceptions.UniqueViolationError:
                pass
            finally:
                await tr.rollback()

        total = await c.fetchval("SELECT COUNT(*) FROM photos")
        print(f"ok - no duplicate photos ({total:,} rows, unique index enforcing)")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
