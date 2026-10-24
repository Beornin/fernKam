"""us_counties: a GPS point's county and state by the Census boundary it falls in.

Needs the boundary file (downloaded once into data/geo on first use).
Run: uv run python tests/test_us_counties.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam import us_counties


def main() -> None:
    if not us_counties.available():
        print("skipped - no boundary file and no download")
        return
    got = us_counties.lookup([
        (25.7617, -80.1918),    # Miami
        (30.4383, -84.2807),    # Tallahassee
        (24.5551, -81.7800),    # Key West: an island, its own ring
        (33.7490, -84.3880),    # Atlanta
        (30.0, -80.5),          # offshore Atlantic, ~60 km out
        (19.43, -99.13),        # Mexico City
        (25.7907, -80.1280),    # the sand on Miami Beach, seaward of the shoreline-clipped boundary
    ])
    assert got == [("Miami-Dade County", "Florida"), ("Leon County", "Florida"), ("Monroe County", "Florida"),
                   ("Fulton County", "Georgia"), None, None, ("Miami-Dade County", "Florida")], got
    assert us_counties.lookup([]) == []
    # Windsor, Canada, across the river from Detroit: only a point whose nearest town is in the US
    # may take a county from just past the boundary.
    windsor = [(42.3149, -83.0364)]
    assert us_counties.lookup(windsor, near_shore=[False]) == [None]

    from fernkam.api.routers.geocode import _county
    assert _county({"cc": "GB", "admin2": "Example District"}, None, True) == "Example District"
    assert _county({"cc": "GB", "admin2": ""}, None, True) == ""
    assert _county({"cc": "US", "admin2": "Wayne County"}, ("Leon County", "Florida"), True) == "Leon County"
    assert _county({"cc": "US"}, None, True) == "" and _county({"cc": "US"}, None, False) is None
    print("ok - counties by boundary; offshore and abroad are in none")


if __name__ == "__main__":
    main()
