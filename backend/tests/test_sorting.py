"""Check Sort: shoots move whole to AC_SORTED/YYYY/MM/<shoot>, loose files by date.

Run directly: python backend/tests/test_sorting.py
"""
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.workflows.sorting_video import plan, run


def touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        raw, sort_me, out = t / "AA_RAW", t / "SORT ME", t / "AC_SORTED"
        shoot = raw / "20260925_00029"
        nef = touch(shoot / "_DSC1.NEF")
        jpg = touch(shoot / "jpg" / "_DSC1.jpg")
        mov = touch(shoot / "DSC_2.MOV")
        lost = touch(shoot / "jpg" / "_DSC3.jpg")          # no camera date: goes with its shoot
        reset = touch(shoot / "_DSC4.NEF")                 # clock reset: the shoot's month wins
        blank = touch(raw / "20260101_00001" / "x.NEF")    # a shoot with no dates at all
        dump = touch(raw / "DSC_9.jpg")                    # loose in AA_RAW
        dump_lost = touch(raw / "DSC_8.jpg")               # loose, no date
        phone = touch(sort_me / "Trip" / "PXL_20250704_1.jpg")
        stays = touch(sort_me / "scan.jpg")                # loose in SORT ME, no date
        sep = datetime(2026, 9, 25)
        moves, undated, already = plan(str(raw), str(sort_me), str(out), dates={
            nef: sep, jpg: sep, mov: sep, reset: datetime(2000, 1, 1), dump: datetime(2026, 8, 2)})
        got = {s: d.relative_to(t).as_posix() for s, d in moves + undated}
        assert got == {
            nef: "AC_SORTED/2026/09/20260925_00029/_DSC1.NEF",
            jpg: "AC_SORTED/2026/09/20260925_00029/jpg/_DSC1.jpg",
            mov: "AC_SORTED/2026/09/20260925_00029/DSC_2.MOV",
            lost: "AC_SORTED/2026/09/20260925_00029/jpg/_DSC3.jpg",
            reset: "AC_SORTED/2026/09/20260925_00029/_DSC4.NEF",
            blank: "SORT ME/20260101_00001/x.NEF",               # pulled, waits whole
            dump: "AC_SORTED/2026/08/DSC_9.jpg",                 # loose: just by date
            dump_lost: "SORT ME/DSC_8.jpg",
            phone: "AC_SORTED/2025/07/Trip/PXL_20250704_1.jpg",
            stays: "SORT ME/scan.jpg",
        }, got
        assert not already

        # Apply moves the files and removes the shoot folders it emptied.
        shutil.rmtree(raw)
        stays.unlink()
        a = touch(raw / "20260926_00030" / "PXL_20260926_a.NEF")
        b = touch(raw / "20260926_00030" / "jpg" / "PXL_20260926_a.jpg")
        run(str(raw), str(sort_me), str(out), dry_run=False)
        s = out / "2026/09/20260926_00030"
        assert (s / "PXL_20260926_a.NEF").is_file() and (s / "jpg/PXL_20260926_a.jpg").is_file()
        assert not a.exists() and not b.exists()
        assert raw.is_dir() and not any(raw.iterdir()), list(raw.rglob("*"))
        assert sort_me.is_dir() and not any(sort_me.iterdir()), list(sort_me.rglob("*"))
    print("ok - shoots move whole to YYYY/MM/<shoot>, loose files by date, undated wait in SORT ME")


if __name__ == "__main__":
    main()
