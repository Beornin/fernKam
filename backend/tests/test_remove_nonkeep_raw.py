"""Check which RAWs "Remove non-keep RAW" would send to the Trash.

It used to look for a RAW's picture only in the RAW's own folder and its jpg/
subfolder. Portfolio keeps RAWs in Album/RAW/ beside Album/x.jpg (see
promote_destination), so pointed at Portfolio it would have trashed every
Portfolio RAW: 2,666 of the live library's 3,560.

Run directly: python backend/tests/test_remove_nonkeep_raw.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.workflows.remove_nonkeep_raw import select_orphans


def main() -> None:
    pics = ["Portfolio/Owls/barred.jpg", "AA_RAW/shoot1/jpg/DSC_0001.jpg",
            "AA_RAW/shoot1/DSC_0002-Edit.jpg", "Trip/RAW/jpg/far.jpg"]
    by_dir = defaultdict(set)
    for p in map(Path, pics):
        by_dir[p.parent].add(p.stem.lower())

    raws = {
        "Portfolio/Owls/RAW/barred.NEF": False,   # picture in the parent: keep
        "AA_RAW/shoot1/DSC_0001.NEF": False,       # picture in jpg/: keep
        "AA_RAW/shoot1/DSC_0002.NEF": False,       # edited derivative: keep
        "Trip/RAW/far.NEF": False,                 # RAW/jpg/: keep
        "AA_RAW/shoot1/DSC_0003.NEF": True,        # no picture anywhere: trash
        "Portfolio/Owls/RAW/snowy.NEF": True,      # no picture in RAW/ or parent: trash
        "Portfolio/Owls/Other/RAW/barred.NEF": True,  # a different album's picture doesn't count
    }
    got = {str(p).replace("\\", "/") for p in select_orphans([Path(r) for r in raws], by_dir)}
    want = {r for r, trash in raws.items() if trash}
    assert got == want, f"trashed {sorted(got)}, expected {sorted(want)}"
    print("ok - remove non-keep RAW keeps Portfolio RAW/ pairs, trashes true orphans")


if __name__ == "__main__":
    main()
