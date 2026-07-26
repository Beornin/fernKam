"""Tiny generic key/value store for user-adjustable runtime settings.

Deliberately not a full ORM model — this is just get/set against a two-column
table (see alembic/versions/0020_app_settings.py), used today for the face
auto-confirm "sensitivity" slider so it survives restarts without needing an
env var edit.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text


async def get_setting(db, key: str, default: Optional[str] = None) -> Optional[str]:
    row = (await db.execute(
        text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}
    )).first()
    return row[0] if row else default


async def set_setting(db, key: str, value: str) -> None:
    await db.execute(text("""
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (:key, :value, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
    """), {"key": key, "value": value})
    await db.commit()
