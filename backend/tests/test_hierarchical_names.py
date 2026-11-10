"""hierarchical_names: the keyword path written into a file uses tag names, and reads back to the
same tag (never a second one).

Run: uv run python tests/test_hierarchical_names.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.metadata_sync import hierarchical_names
from fernkam.sync_merge import file_tag_paths, tag_key


def roundtrip(path: str, names: dict) -> str:
    written = hierarchical_names(path, names).replace("/", "|")           # build_photo_payload
    read = file_tag_paths({"tag_paths": [written.replace("|", "/")]})      # metadata read, then sync_merge
    return tag_key(read[0])


def main() -> None:
    names = {
        "Wildlife": "Wildlife",
        "Wildlife.Frogs": "Frogs",
        "Wildlife.Frogs.Cope_s_Gray_Treefrog__Dryophytes_chrysoscelis_": "Cope's Gray Treefrog (Dryophytes chrysoscelis)",
        "Wildlife.Frogs.Old_Label": "Renamed since (Latin added)",   # path no longer matches its name
        "Wildlife.Bats.A_B": "Pteronotus a/b",                         # a name with a separator
        "Wildlife.Bats": "Bats",
    }
    p = "Wildlife.Frogs.Cope_s_Gray_Treefrog__Dryophytes_chrysoscelis_"
    assert hierarchical_names(p, names) == "Wildlife/Frogs/Cope's Gray Treefrog (Dryophytes chrysoscelis)"
    assert hierarchical_names("Wildlife.Frogs.Old_Label", names) == "Wildlife/Frogs/Old_Label"      # keeps the label
    assert hierarchical_names("Wildlife.Bats.A_B", names) == "Wildlife/Bats/A_B"                      # "/" in a name keeps the label
    assert hierarchical_names("Unknown.Path", {}) == "Unknown/Path"                                   # no names known
    for path in (p, "Wildlife.Frogs.Old_Label", "Wildlife.Bats.A_B", "Unknown.Path"):
        assert roundtrip(path, names) == path, path                                                    # same tag back
    print("ok - keyword paths use names and read back to the same tag")


if __name__ == "__main__":
    main()
