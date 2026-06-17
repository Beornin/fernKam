"""Idempotent migration for the `app_logs` table + indexes.

Runs from app lifespan. Safe to call on every start.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def ensure_app_logs(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS app_logs (
                id           BIGSERIAL PRIMARY KEY,
                ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
                level        SMALLINT NOT NULL,
                level_name   VARCHAR(10) NOT NULL,
                source       VARCHAR(64) NOT NULL,
                logger_name  VARCHAR(128),
                message      TEXT NOT NULL,
                file         VARCHAR(255),
                line         INTEGER,
                func         VARCHAR(128),
                exc_info     TEXT,
                context      JSONB,
                fingerprint  VARCHAR(64) NOT NULL,
                occurrences  INTEGER NOT NULL DEFAULT 1,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_app_logs_ts ON app_logs (ts DESC)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_app_logs_level ON app_logs (level)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_app_logs_source ON app_logs (source)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_app_logs_fp_recent ON app_logs (fingerprint, last_seen_at DESC)"
        ))
        # Best-effort full-text index — pg_trgm extension is already present.
        try:
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_app_logs_msg_trgm ON app_logs USING gin (message gin_trgm_ops)"
            ))
        except Exception as e:
            logger.warning("[app_logs] trgm index skipped: %s", e)
    logger.info("[app_logs] table + indexes ready")
