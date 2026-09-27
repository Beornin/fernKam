"""Check Sort: AA_RAW and SORT ME go to AC_SORTED/YYYY/MM, format folders kept.

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
        nef = touch(raw / "20260925_00029" / "_DSC1.NEF")
        jpg = touch(raw / "20260925_00029" / "jpg" / "_DSC1.jpg")
        mov = touch(raw / "20260925_00029" / "DSC_2.MOV")
        lost = touch(raw / "20260925_00029" / "jpg" / "_DSC3.jpg")      # no camera date
        phone = touch(sort_me / "Trip" / "PXL_20250704_1.jpg")           # date from the name
        stays = touch(sort_me / "scan.jpg")                             # no camera date
        sep = datetime(2026, 9, 25)
        moves, undated, already = plan(str(raw), str(sort_me), str(out),
                                       dates={nef: sep, jpg: sep, mov: sep})
        got = {s: d.relative_to(t).as_posix() for s, d in moves + undated}
        assert got == {
            nef: "AC_SORTED/2026/09/_DSC1.NEF",
            jpg: "AC_SORTED/2026/09/jpg/_DSC1.jpg",                     # jpg/ kept beside its RAW
            mov: "AC_SORTED/2026/09/DSC_2.MOV",
            phone: "AC_SORTED/2025/07/PXL_20250704_1.jpg",               # plain folders flattened
            lost: "SORT ME/20260925_00029/jpg/_DSC3.jpg",                # pulled, folders kept
            stays: "SORT ME/scan.jpg",
        }, got
        assert not already

        # Apply moves the files and removes the shoot folders it emptied.
        shutil.rmtree(nef.parent)
        stays.unlink()
        a = touch(raw / "20260926_00030" / "PXL_20260926_a.NEF")
        b = touch(raw / "20260926_00030" / "jpg" / "PXL_20260926_a.jpg")
        run(str(raw), str(sort_me), str(out), dry_run=False)
        assert (out / "2026/09/PXL_20260926_a.NEF").is_file() and (out / "2026/09/jpg/PXL_20260926_a.jpg").is_file()
        assert not a.exists() and not b.exists()
        assert raw.is_dir() and not any(raw.iterdir()), list(raw.rglob("*"))
        assert sort_me.is_dir() and not any(sort_me.iterdir()), list(sort_me.rglob("*"))
    print("ok - sort keeps jpg/ with its RAW, pulls undated into SORT ME, prunes emptied folders")


if __name__ == "__main__":
    main()
