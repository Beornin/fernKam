import sqlalchemy as sa

urls = [
    "postgresql://postgres:fernkam@localhost:5432/postgres",
    "postgresql://postgres:fernKam@localhost:5432/postgres",
    "postgresql://postgres:postgres@localhost:5432/postgres",
    "postgresql://budget_user:ben@localhost:5432/postgres",
]

for url in urls:
    try:
        e = sa.create_engine(url)
        with e.connect() as c:
            user = c.execute(sa.text("SELECT current_user, usesuper FROM pg_user WHERE usename=current_user")).fetchone()
            print(f"OK  {url.split('@')[0].split('//')[1].split(':')[0]} -> superuser={user[1]}")
    except Exception as ex:
        msg = str(ex)[:100]
        print(f"FAIL {url.split('@')[0].split('//')[1].split(':')[0]} -> {msg}")
