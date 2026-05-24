import sqlalchemy as sa

e = sa.create_engine("postgresql+psycopg2://fernkam_user:ben@localhost:5432/fernkam")
with e.connect() as c:
    tables = c.execute(sa.text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )).fetchall()
    print("Tables:")
    for r in tables:
        count = c.execute(sa.text(f"SELECT COUNT(*) FROM {r[0]}")).scalar()
        print(f"  {r[0]:30s} rows={count}")

    indexes = c.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY indexname"
    )).fetchall()
    print(f"\nIndexes ({len(indexes)} total):")
    for r in indexes:
        print(f"  {r[0]}")

    ver = c.execute(sa.text("SELECT version_num FROM alembic_version")).fetchall()
    print(f"\nAlembic version: {[r[0] for r in ver]}")
