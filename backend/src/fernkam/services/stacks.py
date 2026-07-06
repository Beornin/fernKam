"""RAW/JPG stack detection.

A "stack" groups one RAW file (living in an `<album>/RAW/` subfolder) with
every derivative (JPG/TIF/edited variant) in the parent album whose filename
stem starts with the RAW's stem — e.g. `_DSC9498.NEF` <-> `_DSC9498.jpg`, or
`_5_I5460.CR3` <-> `_5_I5460-DxO_DeepPRIME XD2s.jpg`.

Only groups containing >=1 RAW photo are considered stacks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.db.models.photos import Photo, PhotoStack
from fernkam.media_types import is_raw

# Separator characters allowed right after the RAW stem in a derivative's
# filename, to avoid `_DSC95` false-matching `_DSC950x`.
_SEPARATORS = (".", "-", "_", " ")


def _stem(filename: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[0].lower()
    return filename.lower()


def _parent_album(raw_album_path: str) -> Optional[str]:
    """Return the parent album for a `.../RAW` album path, or None if it doesn't end in /RAW."""
    normalized = raw_album_path.rstrip("/")
    if normalized.endswith("/RAW"):
        parent = normalized[: -len("/RAW")]
        return parent if parent else "/"
    if normalized == "RAW":
        return "/"
    return None


def _derivative_matches(raw_stem: str, derivative_stem: str) -> bool:
    """True if derivative_stem is raw_stem itself, or raw_stem followed by a separator."""
    if derivative_stem == raw_stem:
        return True
    if derivative_stem.startswith(raw_stem):
        next_char = derivative_stem[len(raw_stem): len(raw_stem) + 1]
        return next_char in _SEPARATORS
    return False


def _pick_cover(raw_photo: Photo, derivatives: list[Photo]) -> Photo:
    """Prefer the best derivative: highest rating, then largest file, else the RAW."""
    if not derivatives:
        return raw_photo
    return max(
        derivatives,
        key=lambda p: (p.rating or 0, p.file_size or 0),
    )


async def detect_stacks(db: AsyncSession, album_path: Optional[str] = None) -> dict:
    """(Re)build stacks for the whole library, or restricted to one album subtree.

    Idempotent: safe to re-run; re-syncs membership, cover, and counts.
    Returns summary stats.
    """
    photo_q = select(Photo).where(Photo.status == 1)
    if album_path:
        photo_q = photo_q.where(Photo.album_path.like(f"{album_path}%"))
    all_photos = (await db.execute(photo_q)).scalars().all()

    # Index photos by album_path for fast derivative lookup.
    by_album: dict[str, list[Photo]] = {}
    for p in all_photos:
        by_album.setdefault(p.album_path, []).append(p)

    raw_photos = [p for p in all_photos if is_raw(p.filename)]

    stacks_created = 0
    stacks_updated = 0
    photos_grouped = 0
    now = datetime.now(timezone.utc)

    seen_stack_keys: set[tuple[str, str]] = set()

    for raw in raw_photos:
        parent_album = _parent_album(raw.album_path)
        if parent_album is None:
            continue
        siblings = by_album.get(parent_album, [])
        raw_stem = _stem(raw.filename)
        derivatives = [
            p for p in siblings
            if not is_raw(p.filename) and _derivative_matches(raw_stem, _stem(p.filename))
        ]
        if not derivatives:
            # RAW with no derivative — still forms a single-member stack so it's
            # discoverable and can later be tag-synced once a derivative appears.
            derivatives = []

        stack_key = (parent_album, raw_stem)
        seen_stack_keys.add(stack_key)

        cover = _pick_cover(raw, derivatives)
        members = [raw, *derivatives]

        existing = (await db.execute(
            select(PhotoStack).where(
                PhotoStack.album_path == parent_album,
                PhotoStack.stem_key == raw_stem,
            )
        )).scalar_one_or_none()

        if existing is None:
            existing = PhotoStack(
                album_path=parent_album,
                stem_key=raw_stem,
                cover_photo_id=cover.id,
                member_count=len(members),
                has_raw=True,
                created_at=now,
                updated_at=now,
            )
            db.add(existing)
            await db.flush()
            stacks_created += 1
        else:
            existing.cover_photo_id = cover.id
            existing.member_count = len(members)
            existing.has_raw = True
            existing.updated_at = now
            stacks_updated += 1

        for m in members:
            m.stack_id = existing.id
            m.stack_role = "raw" if is_raw(m.filename) else "derivative"
            photos_grouped += 1

    # Clean up stacks that no longer have any matching RAW (e.g. RAW moved/deleted).
    all_stacks_q = select(PhotoStack)
    if album_path:
        all_stacks_q = all_stacks_q.where(PhotoStack.album_path.like(f"{album_path}%"))
    all_stacks = (await db.execute(all_stacks_q)).scalars().all()
    stale_ids = [s.id for s in all_stacks if (s.album_path, s.stem_key) not in seen_stack_keys]
    if stale_ids:
        await db.execute(update(Photo).where(Photo.stack_id.in_(stale_ids)).values(stack_id=None, stack_role=None))
        for s in all_stacks:
            if s.id in stale_ids:
                await db.delete(s)

    await db.commit()

    return {
        "raw_photos_scanned": len(raw_photos),
        "stacks_created": stacks_created,
        "stacks_updated": stacks_updated,
        "stacks_removed": len(stale_ids),
        "photos_grouped": photos_grouped,
    }
