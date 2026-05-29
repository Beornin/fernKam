"""Check if pgvector extension is available and enable it."""
import psycopg2
from fernkam.config import get_settings

s = get_settings()
url = s.pg_url_sync.replace("postgresql+psycopg2://", "postgresql://")
conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT name FROM pg_available_extensions WHERE name='vector'")
row = cur.fetchone()
if not row:
    print("pgvector extension is NOT available in this PostgreSQL installation.")
    print("To install: https://github.com/pgvector/pgvector")
    print("On Windows with PostgreSQL 17: download the pre-built .zip from pgvector releases")
else:
    print("pgvector extension is available! Enabling...")
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("pgvector enabled successfully.")
    except psycopg2.errors.InsufficientPrivilege:
        print("Need superuser to CREATE EXTENSION. Run as postgres user:")
        print("  psql -U postgres -d fernkam -c \"CREATE EXTENSION vector\"")
