"""Idempotent migration for the `tasks` table + indexes.

Runs from app lifespan. Safe to call on every start.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_tasks(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id           VARCHAR(36) PRIMARY KEY,
                task_type    VARCHAR(64) NOT NULL,
                status       VARCHAR(32) NOT NULL DEFAULT 'running',
                message      TEXT NOT NULL DEFAULT '',
                started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ,
                progress     JSONB
            )
            """
        ))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks (status)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_started_at ON tasks (started_at DESC)"))
