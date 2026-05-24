import sqlalchemy as sa

e = sa.create_engine("mysql+pymysql://root@localhost:3306/digikam")
with e.connect() as c:
    # Check what property values exist
    props = c.execute(sa.text(
        "SELECT property, COUNT(*) as cnt FROM ImageTagProperties GROUP BY property ORDER BY cnt DESC LIMIT 20"
    )).fetchall()
    print("ImageTagProperties.property values:")
    for r in props:
        print(f"  {r[0]:40s} {r[1]:>8,}")

    # Sample a few face rows
    sample = c.execute(sa.text(
        "SELECT imageid, tagid, property, LEFT(value,80) as val "
        "FROM ImageTagProperties LIMIT 5"
    )).fetchall()
    print("\nSample rows:")
    for r in sample:
        print(f"  img={r[0]} tag={r[1]} prop={r[2]} val={r[3]}")
