"""Postgres-level maintenance: size/bloat stats, VACUUM ANALYZE, REINDEX CONCURRENTLY.

VACUUM and REINDEX CONCURRENTLY cannot run inside a transaction block, so both
use an AUTOCOMMIT connection — same pattern as the partial HNSW index in
db/index_setup.py.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Large/critical indexes worth an explicit manual REINDEX (not every index —
# just the ones that see heavy write churn or vector search traffic).
REINDEX_CANDIDATES = [
    "ix_faces_emb_unconfirmed_hnsw",
    "ix_faces_embedding_v_confirmed_hnsw",
    "ix_photos_exif_gin",
]


async def get_db_stats(engine: AsyncEngine) -> dict:
    async with engine.connect() as conn:
        db_size = (await conn.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()

        table_rows = (await conn.execute(text("""
            SELECT
                relname AS table_name,
                n_live_tup,
                n_dead_tup,
                last_vacuum,
                last_autovacuum,
                last_analyze,
                last_autoanalyze,
                pg_total_relation_size(relid) AS total_size
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 10
        """))).mappings().all()

        tables = []
        for row in table_rows:
            live = row["n_live_tup"] or 0
            dead = row["n_dead_tup"] or 0
            dead_pct = (dead / (live + dead) * 100.0) if (live + dead) > 0 else 0.0
            last_vacuum = row["last_vacuum"] or row["last_autovacuum"]
            tables.append({
                "table_name": row["table_name"],
                "live_rows": live,
                "dead_rows": dead,
                "dead_pct": round(dead_pct, 1),
                "last_vacuum": last_vacuum.isoformat() if last_vacuum else None,
                "total_size": row["total_size"],
            })

        overall_dead = sum(t["dead_rows"] for t in tables)
        overall_live = sum(t["live_rows"] for t in tables)
        overall_pct = (overall_dead / (overall_live + overall_dead) * 100.0) if (overall_live + overall_dead) > 0 else 0.0

        return {
            "db_size_bytes": db_size,
            "dead_row_pct": round(overall_pct, 1),
            "tables": tables,
        }


async def run_vacuum_analyze(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await ac.execute(text("VACUUM (ANALYZE)"))


async def run_reindex_concurrently(engine: AsyncEngine, index_names: list[str]) -> dict:
    results: dict[str, str] = {}
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name in index_names:
            try:
                await ac.execute(text(f"REINDEX INDEX CONCURRENTLY {name}"))
                results[name] = "ok"
            except Exception as exc:
                results[name] = f"skipped: {exc}"
    return results
