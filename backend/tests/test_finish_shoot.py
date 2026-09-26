"""Check what Finish shoot bins and where it files the keepers, and what
Sorting holds back.

Finish shoot sends files to the Recycle Bin and moves the rest out of the
intake, so each rule gets a case: a deleted JPG, a red label, a derivative
that keeps its RAW alive, P/L overrides, a name clash (the whole keeper
stays), and a file with no date.

Run directly: python backend/tests/test_finish_shoot.py
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.workflows.finish_shoot import BLUE, GREEN, RED, plan
from fernkam.workflows import sorting_video


def touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        shoot = lib / "AA_RAW" / "20260925_00001"
        f = {n: touch(shoot / n) for n in ("a.NEF", "b.NEF", "c.NEF", "d.NEF", "e.NEF", "f.NEF", "g.NEF", "h.NEF", "clip.MOV")}
        j = {n: touch(shoot / "jpg" / n) for n in ("a.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg", "g-Edit.jpg", "h.jpg")}
        # b.jpg was deleted in the cull; c is labelled red; d green (P), e blue (L)
        labels = {j["c.jpg"]: RED, j["d.jpg"]: GREEN, j["e.jpg"]: BLUE}
        when = datetime(2026, 9, 25, 11, 13)
        dates = {x: when for x in [*f.values(), *j.values()] if x.stem != "h"}   # h has no date
        touch(lib / "Ordered by Dates" / "2026" / "09" / "f.NEF")                 # f clashes

        p = plan(shoot, "dates", labels=labels, dates=dates, library_root=lib,
                 archive="Ordered by Dates", portfolio_folder="Portfolio/Frogs", client_folder=str(Path(td) / "client"))
        names = lambda xs: sorted(x.name for x in xs)
        assert names(p.trash) == ["b.NEF", "c.NEF", "c.jpg"], names(p.trash)
        moves = {s.name: d.relative_to(td).as_posix() for s, d in p.moves}
        dated = "lib/Ordered by Dates/2026/09/"
        assert moves == {
            "a.jpg": dated + "a.jpg", "a.NEF": dated + "a.NEF",                       # default: by date
            "d.jpg": "lib/Portfolio/Frogs/d.jpg", "d.NEF": "lib/Portfolio/Frogs/RAW/d.NEF",  # P
            "e.jpg": "client/e.jpg", "e.NEF": dated + "e.NEF",                        # L: JPG out, RAW kept
            "g-Edit.jpg": dated + "g-Edit.jpg", "g.NEF": dated + "g.NEF",             # derivative keeps g
        }, moves
        held = {x.name for x, _ in p.held}
        assert held == {"f.jpg", "f.NEF", "h.jpg", "h.NEF"}, p.held     # clash: whole keeper stays; no date
        assert names(p.clear_labels) == ["d.jpg", "e.jpg"], p.clear_labels
        assert p.counts == {"dates": 2, "portfolio": 1, "client": 1}, p.counts

        # Portfolio as the shoot's destination needs a folder; without one nothing moves.
        p = plan(shoot, "portfolio", labels={}, dates=dates, library_root=lib, archive="Ordered by Dates")
        assert not p.moves and p.held, p

        # Sorting: Pixel names date themselves, camera dates come from exiftool
        # (passed in here), no date means it stays; same name+size = already there.
        sort_me, raw = Path(td) / "SORT ME", Path(td) / "AA_RAW"
        pxl = touch(sort_me / "PXL_20251220_224321294.jpg")
        cam = touch(sort_me / "DSC_0001.JPG")
        nodate = touch(sort_me / "scan.jpg")
        vid = touch(raw / "shoot" / "clip.MOV")
        out = Path(td) / "Ordered"
        touch(out / "2026" / "09" / "DSC_0001.JPG")      # same size: already there
        moves, undated, already = sorting_video.plan(
            str(raw), str(sort_me), str(out), dates={cam: when, nodate: None, vid: datetime(2025, 7, 4)})
        got = {s.name: d.relative_to(out).as_posix() for s, d in moves}
        assert got == {"PXL_20251220_224321294.jpg": "2025/12/PXL_20251220_224321294.jpg",
                       "clip.MOV": "2025/07/clip.MOV"}, got
        assert undated == [nodate] and [s for s, _ in already] == [cam], (undated, already)

    print("ok - finish shoot bins rejects, files keepers by P/L/default, holds clashes and undated; sorting holds undated")


if __name__ == "__main__":
    main()
