import sys

print(f"Python: {sys.version}")

try:
    import psycopg2
    conn = psycopg2.connect("postgresql://budget_user:ben@localhost:5432/postgres")
    cur = conn.cursor()
    cur.execute("SELECT version()")
    print("PG version:", cur.fetchone()[0])
    cur.execute("SELECT name, default_version FROM pg_available_extensions WHERE name IN ('vector','postgis','ltree','pg_trgm') ORDER BY name")
    rows = cur.fetchall()
    found = {r[0] for r in rows}
    for r in rows:
        print(f"  [OK] Extension {r[0]}: v{r[1]}")
    for needed in ['vector','postgis','ltree','pg_trgm']:
        if needed not in found:
            print(f"  [MISSING] Extension {needed} not available")
    conn.close()
except ImportError:
    print("psycopg2 not installed — installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
    print("Installed. Re-run this script.")
except Exception as e:
    print(f"Postgres connection error: {e}")
