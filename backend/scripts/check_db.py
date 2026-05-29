import psycopg2
from fernkam.config import get_settings

s = get_settings()
url = s.pg_url_sync.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
conn = psycopg2.connect(url)
cur = conn.cursor()

cur.execute("SELECT extname FROM pg_extension WHERE extname='vector'")
print("pgvector:", cur.fetchone())

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='faces' ORDER BY ordinal_position")
print("faces cols:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT COUNT(*) FROM faces WHERE status='confirmed'")
print("confirmed faces:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM people")
print("people:", cur.fetchone()[0])
