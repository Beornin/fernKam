import sqlalchemy as sa

e = sa.create_engine("postgresql://budget_user:ben@localhost:5432/postgres")
with e.connect() as c:
    dbs = c.execute(sa.text("SELECT datname FROM pg_database ORDER BY datname")).fetchall()
    print("Databases:", [r[0] for r in dbs])
    users = c.execute(sa.text("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles ORDER BY rolname")).fetchall()
    print("Roles:")
    for r in users:
        print(f"  {r[0]:30s} super={r[1]} createdb={r[2]} createrole={r[3]}")
