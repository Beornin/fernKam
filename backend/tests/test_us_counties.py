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
    print("ok - counties by boundary; offshore and abroad are in none")


if __name__ == "__main__":
    main()
