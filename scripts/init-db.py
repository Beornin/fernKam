"""
One-time DB setup script.
Usage:
    python scripts/init-db.py --pg-url "postgresql://budget_user:ben@localhost:5432/postgres"
    python scripts/init-db.py --pg-url "postgresql://postgres:<password>@localhost:5432/postgres"
"""
import argparse
import sys

import sqlalchemy as sa


def main():
    parser = argparse.ArgumentParser(description="Create fernkam DB + user")
    parser.add_argument("--pg-url", default="postgresql://budget_user:ben@localhost:5432/postgres")
    parser.add_argument("--password", default="fernkam2024", help="Password to set for fernkam_user")
    args = parser.parse_args()

    engine = sa.create_engine(args.pg_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        # Check superuser
        row = conn.execute(sa.text("SELECT usesuper, usecreatedb FROM pg_user WHERE usename = current_user")).fetchone()
        print(f"Connected as: {conn.execute(sa.text('SELECT current_user')).scalar()}")
        print(f"  superuser={row.usesuper}, createdb={row.usecreatedb}")

        # Create user
        exists = conn.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname='fernkam_user'")).fetchone()
        if not exists:
            conn.execute(sa.text(f"CREATE USER fernkam_user WITH PASSWORD '{args.password}'"))
            print("✓ Created user fernkam_user")
        else:
            print("! User fernkam_user already exists — skipping creation")

        # Create DB
        db_exists = conn.execute(sa.text("SELECT 1 FROM pg_database WHERE datname='fernkam'")).fetchone()
        if not db_exists:
            conn.execute(sa.text("CREATE DATABASE fernkam OWNER fernkam_user"))
            print("✓ Created database fernkam")
        else:
            print("! Database fernkam already exists — skipping")

    # Connect to fernkam DB and create extensions
    base_url = args.pg_url.rsplit("/", 1)[0]
    fk_engine = sa.create_engine(f"{base_url}/fernkam", isolation_level="AUTOCOMMIT")
    with fk_engine.connect() as conn:
        for ext in ("ltree", "pg_trgm"):
            try:
                conn.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
                print(f"✓ Extension {ext} enabled")
            except Exception as e:
                print(f"! Extension {ext} failed: {e}")

        for ext in ("vector", "postgis"):
            available = conn.execute(
                sa.text("SELECT 1 FROM pg_available_extensions WHERE name = :n"),
                {"n": ext}
            ).fetchone()
            if available:
                try:
                    conn.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
                    print(f"✓ Extension {ext} enabled")
                except Exception as e:
                    print(f"! Extension {ext} failed: {e}")
            else:
                print(f"  Extension {ext} not available — install separately (see README)")

        conn.execute(sa.text("GRANT ALL ON SCHEMA public TO fernkam_user"))
        conn.execute(sa.text("GRANT ALL PRIVILEGES ON DATABASE fernkam TO fernkam_user"))
        print("✓ Grants applied")

    print("\nDone. Update backend/.env with:")
    print(f"  PG_URL=postgresql+asyncpg://fernkam_user:{args.password}@localhost:5432/fernkam")
    print(f"  PG_URL_SYNC=postgresql+psycopg2://fernkam_user:{args.password}@localhost:5432/fernkam")


if __name__ == "__main__":
    main()
