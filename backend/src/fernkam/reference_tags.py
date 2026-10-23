"""Reference fact tags: Reference › REF-272036 for the user's photos (REF_PREFIX), ARC-272036 for an
inherited archive's (ARCHIVE_REF_PREFIX, the photos under ARCHIVE_FOLDER).

The licensing Ref on Flickr and in exported copies. Given once, to a photo that goes public, and
written into the file like any tag (DB → Files), so it survives renames, moves and even a rebuilt
database. The number starts as the photo's id, but the tag is the record: it is never recomputed,
and a new Ref skips numbers already taken. The NEF, TIF and JPG of one image share a Ref (a stack
gets one; a copied file brings its own along).
"""
from __future__ import annotations

import re

from sqlalchemy import text

ROOT = "Reference"
_NUM = re.compile(r"^[A-Z]{1,6}-(\d+)$")


def in_archive(album_path: str) -> bool:
    """The inherited archive's photos all live under ARCHIVE_FOLDER; everything else is the user's."""
    from fernkam.config import get_settings
    archive = get_settings().archive_folder.strip("/")
    return bool(archive) and album_path.split("/", 1)[0] == archive


def prefix(album_path: str) -> str:
    from fernkam.config import get_settings
    s = get_settings()
    return s.archive_ref_prefix if in_archive(album_path) else s.ref_prefix


def number(photo_id: int, taken: set[int]) -> int:
    """The photo's id, unless a Ref already uses it (after a rebuilt database): then the next free one."""
    return photo_id if photo_id not in taken else max(taken) + 1


async def ensure(db, photo_ids: list[int], assign: bool = True) -> dict[int, str]:
    """Each photo's Ref ({photo id: "REF-272036"}). With assign, photos without one get one and are
    flagged for write-back; without, only existing Refs come back. Does not commit."""
    from fernkam.sync_merge import ensure_tag_path

    have: dict[int, str] = {}
    for pid, name in (await db.execute(text("""
            SELECT x.photo_id, t.name FROM photo_tags x JOIN tags t ON t.id = x.tag_id
            WHERE t.path <@ 'Reference' AND nlevel(t.path) = 2 AND x.photo_id = ANY(:ids)
            ORDER BY t.name DESC"""), {"ids": list(photo_ids)})).all():
        have[pid] = name   # two Refs on one file: always the same one wins
    missing = [p for p in photo_ids if p not in have]
    if not assign or not missing:
        return have
    taken = {int(m[1]) for (n,) in (await db.execute(text("SELECT name FROM tags WHERE path <@ 'Reference'"))).all()
             if (m := _NUM.match(n))}
    cache: dict = {}
    rows = (await db.execute(text("SELECT id, album_path, stack_id FROM photos WHERE id = ANY(:ids) ORDER BY id"),
                             {"ids": missing})).all()
    # The NEF, TIF and JPG of one image (a stack) share its Ref: a member's existing one, or one new one.
    shared = dict((await db.execute(text("""
        SELECT DISTINCT ON (p.stack_id) p.stack_id, t.name FROM photos p
        JOIN photo_tags x ON x.photo_id = p.id JOIN tags t ON t.id = x.tag_id
        WHERE p.stack_id = ANY(:s) AND t.path <@ 'Reference' AND nlevel(t.path) = 2
        ORDER BY p.stack_id, t.name"""), {"s": [r.stack_id for r in rows if r.stack_id]})).all())
    for pid, album, stack in rows:
        if not (name := shared.get(stack)):
            n = number(pid, taken)
            taken.add(n)
            name = f"{prefix(album)}-{n}"
            if stack:
                shared[stack] = name
        tag = await ensure_tag_path(db, [ROOT, name], cache)
        await db.execute(text("INSERT INTO photo_tags (photo_id, tag_id, verified_at) VALUES (:p, :t, now()) "
                              "ON CONFLICT (photo_id, tag_id) DO NOTHING"), {"p": pid, "t": tag.id})
        have[pid] = tag.name
    await db.execute(text("UPDATE tags SET is_fact = true WHERE path <@ 'Reference' AND NOT is_fact"))
    await db.execute(text("UPDATE photos SET file_sync_dirty = true WHERE id = ANY(:ids)"), {"ids": [r.id for r in rows]})
    return have
