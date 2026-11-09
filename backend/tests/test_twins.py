"""is_twins: the one kind of person tag allowed on two faces in a photo, by the word "twins".

Run: uv run python tests/test_twins.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.api.routers.faces._helpers import is_twins


def main() -> None:
    assert is_twins("Twins") and is_twins("A & B - TWINS") and is_twins("the twins")
    assert not is_twins("Twinsley") and not is_twins("Twin Spotted Rattlesnake (Crotalus pricei)")
    assert not is_twins("") and not is_twins(None)
    print("ok - only a twins tag may name two faces in one photo")


if __name__ == "__main__":
    main()
