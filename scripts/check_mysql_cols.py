import sqlalchemy as sa

e = sa.create_engine("mysql+pymysql://root@localhost:3306/digikam")
with e.connect() as c:
    for table in ("ImageInformation", "ImageComments", "ImagePositions", "ImageMetadata", "Images"):
        cols = c.execute(sa.text(f"SHOW COLUMNS FROM {table}")).fetchall()
        print(f"\n{table}: {[r[0] for r in cols]}")
