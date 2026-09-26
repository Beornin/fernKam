"""Postgres-level maintenance: size/bloat stats, VACUUM ANALYZE, REINDEX CONCURRENTLY.

VACUUM and REINDEX CONCURRENTLY cannot run inside a transaction block, so both
use an AUTOCOMMIT connection — same pattern as the partial HNSW index in
db/index_setup.py.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine



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


async def all_indexes(engine: AsyncEngine) -> list[str]:
    """Every index in the schema, smallest first, so the quick B-trees finish
    before the multi-minute vector (HNSW) rebuilds. Read from the database on
    each run: the hand-kept list it replaces still named ix_photos_exif_gin
    (dropped in 0023, so every run said "2/3") and missed the largest indexes."""
    async with engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT i.indexrelid::regclass::text
            FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
            ORDER BY pg_relation_size(i.indexrelid)"""))
        return [r[0] for r in rows]


async def run_reindex_concurrently(engine: AsyncEngine, index_names: list[str], progress=None) -> dict:
    """REINDEX INDEX CONCURRENTLY each index; reads and writes carry on meanwhile.
    A failed rebuild leaves an invalid <name>_ccnew behind, which is dropped."""
    results: dict[str, str] = {}
    async with engine.connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        # A reindex cut short (fernKam closed mid-run) leaves invalid *_ccnew /
        # *_ccold copies that take space and slow writes. Clear them first.
        for (leftover,) in (await ac.execute(text("""
                SELECT indexrelid::regclass::text FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                WHERE NOT i.indisvalid AND c.relname ~ '_cc(new|old)[0-9]*$'"""))).all():
            await ac.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {leftover}"))
            index_names = [n for n in index_names if n != leftover]
        for i, name in enumerate(index_names, 1):
            if progress:
                await progress(i, len(index_names), name)
            try:
                await ac.execute(text(f"REINDEX INDEX CONCURRENTLY {name}"))
                results[name] = "ok"
            except Exception as exc:
                results[name] = f"skipped: {exc}"
                try:
                    await ac.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}_ccnew"))
                except Exception:
                    pass
    return results
