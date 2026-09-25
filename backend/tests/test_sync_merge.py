"""Check the three-way merge between a photo's catalogue metadata and its file.

Before it, a rescan of a file changed by another program simply let the file
win, silently discarding in-app edits that had not been written back yet.

Run directly: python backend/tests/test_sync_merge.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.sync_merge import file_state, file_tag_paths, merge, tag_key


def meta(rating=None, label=None, title=None, caption=None, hier=(), subj=()):
    """A parse_exif_dict()-shaped dict with just the fields the merge reads."""
    return {"rating": rating, "color_label": label, "title": title, "caption": caption,
            "tag_paths": list(hier), "tags": list(subj)}


def db(rating=0, label=None, title=None, caption=None, tags=()):
    return {"rating": rating, "color_label": label, "title": title, "caption": caption, "tags": set(tags)}


def main() -> None:
    base_meta = meta(rating=3, caption="old", hier=["Places/Beach", "People/Jane Doe"])
    base = file_state(base_meta)
    assert base["tags"] == ["People.Jane_Doe", "Places.Beach"], base
    in_sync = db(rating=3, caption="old", tags={"Places.Beach", "People.Jane_Doe"})

    # Nothing changed anywhere.
    m = merge(in_sync, base_meta, base, dirty=False)
    assert not m.updates and not m.add_tags and not m.remove_tags and not m.conflicts, m

    # Changed only in the file (another program): taken.
    f = meta(rating=5, caption="new", hier=["Places/Beach", "Places/Sunset"])
    m = merge(in_sync, f, base, dirty=False)
    assert m.updates == {"rating": 5, "caption": "new"}, m.updates
    assert m.add_tags == [["Places", "Sunset"]], m.add_tags
    assert m.remove_tags == {"People.Jane_Doe"}, m.remove_tags
    assert m.snapshot == file_state(f)

    # Changed only in fernKam (pending write-back): kept.
    edited = db(rating=1, caption="mine", tags={"Places.Beach", "People.Jane_Doe", "Pets.Dog"})
    m = merge(edited, base_meta, base, dirty=True)
    assert not m.updates and not m.add_tags and not m.remove_tags and not m.conflicts, m

    # Changed on both sides: the edit made outside fernKam is honoured, and
    # the fernKam value it replaced is reported.
    m = merge(edited, f, base, dirty=True)
    assert m.updates == {"rating": 5, "caption": "new"}, m.updates
    assert m.conflicts == {"rating": {"fernkam": 1, "file": 5},
                           "caption": {"fernkam": "mine", "file": "new"}}, m.conflicts
    # ...but tags still merge as sets: the file's addition and removal apply,
    # fernKam's own addition (Pets.Dog) stays.
    assert m.add_tags == [["Places", "Sunset"]] and m.remove_tags == {"People.Jane_Doe"}, m

    # Both sides made the same change: not a conflict.
    m = merge(db(rating=5, caption="new", tags=set(base["tags"])), meta(rating=5, caption="new", hier=["Places/Beach", "People/Jane Doe"]), base, dirty=True)
    assert not m.updates and not m.conflicts, m

    # Different edits to different fields: both survive.
    m = merge(db(rating=4, caption="old", tags=set(base["tags"])), meta(rating=3, caption="file", hier=["Places/Beach", "People/Jane Doe"]), base, dirty=True)
    assert m.updates == {"caption": "file"} and not m.conflicts, m

    # Clearing counts as a change: empty caption in the file.
    m = merge(in_sync, meta(rating=3, caption="", hier=["Places/Beach", "People/Jane Doe"]), base, dirty=False)
    assert m.updates == {"caption": None}, m.updates

    # A file that suddenly has none of its metadata (damaged, truncated) is
    # not taken as "everything was cleared": nothing changes, old ancestor kept.
    m = merge(in_sync, meta(), base, dirty=False)
    assert m.lost_all and not m.updates and not m.remove_tags and m.snapshot == base, m

    # No ancestor yet (row not read since synced_meta existed):
    # not dirty -> the file wins for values it has, as scans always did...
    m = merge(db(rating=0, caption="db"), meta(rating=4, caption=None), None, dirty=False)
    assert m.updates == {"rating": 4}, m.updates
    # ...dirty -> pending in-app edits are left alone...
    m = merge(db(rating=2), meta(rating=4), None, dirty=True)
    assert m.updates == {}, m.updates
    # ...and tags are never touched without an ancestor (a file without
    # keywords must not wipe tags imported from digiKam).
    m = merge(db(tags={"Places.Beach"}), meta(), None, dirty=False)
    assert not m.add_tags and not m.remove_tags, m
    # The snapshot is recorded either way, so the next merge has an ancestor.
    assert m.snapshot == file_state(meta())

    # Flat keywords count as top-level tags when there is no hierarchy.
    assert file_tag_paths(meta(subj=["Beach", "Dog"])) == [["Beach"], ["Dog"]]
    assert file_tag_paths(meta(hier=["A/B"], subj=["B", "Face Name"])) == [["A", "B"]]
    assert tag_key(["People", "Jane Doe"]) == "People.Jane_Doe"
    assert tag_key(["People", "Jane_Doe"]) == "People.Jane_Doe"  # idempotent: written-back paths read back equal

    print("ok - three-way metadata merge")


if __name__ == "__main__":
    main()
