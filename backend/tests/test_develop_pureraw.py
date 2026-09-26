"""Check which RAWs "Develop with PureRAW" picks, and how it files the output.

The pick decides what gets developed without anyone looking: a shoot already
being culled must be left alone (its RAWs without a JPG are mostly rejects),
and one still being copied in must wait. The filing decides whether a change
in PureRAW's settings is noticed or silently mis-files hundreds of photos.

Run directly: python backend/tests/test_develop_pureraw.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.workflows.develop_pureraw import QUIET_SECONDS, collect, plan, wrong_outputs


def touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        intake = Path(td) / "AA_RAW"
        a = [touch(intake / "20260925_00001" / n) for n in ("a.NEF", "b.NEF")]      # fresh shoot
        touch(intake / "20260926_00002" / "c.NEF")                                   # being culled:
        d = touch(intake / "20260926_00002" / "d.NEF")                               # c kept, d rejected
        touch(intake / "20260926_00002" / "jpg" / "c.jpg")

        later = time.time() + QUIET_SECONDS + 1
        todo, skipped = plan(intake, whole_intake=True, now=later)
        assert {s.name: sorted(r.name for r in rs) for s, rs in todo.items()} == {"20260925_00001": ["a.NEF", "b.NEF"]}, todo
        assert len(skipped) == 1 and "being culled" in skipped[0], skipped

        # Picking the culled shoot on its own develops what it lacks.
        todo, _ = plan(intake / "20260926_00002", whole_intake=False, now=later)
        assert [r for rs in todo.values() for r in rs] == [d], todo

        # Files still arriving (copied in just now): wait.
        todo, skipped = plan(intake, whole_intake=True, now=time.time())
        assert not todo and any("still arriving" in s for s in skipped), (todo, skipped)

        # Filing: a.jpg is moved into jpg/; b came back as a renamed DNG, which
        # means PureRAW's settings changed. It is reported, not filed.
        shoot = a[0].parent
        todo = {shoot: a}
        before = {shoot: {p.name for p in shoot.iterdir()}}
        touch(shoot / "a.jpg")
        touch(shoot / "a.jpg.tmp")                     # a save in progress is not drift
        assert wrong_outputs(todo, before) == []
        touch(shoot / "b-DxO_DeepPRIME 3.dng")
        assert [p.name for p in wrong_outputs(todo, before)] == ["b-DxO_DeepPRIME 3.dng"]   # stops the run at once
        (shoot / "a.jpg.tmp").unlink()
        developed, missing, unexpected = collect(todo, before)
        assert developed == 1 and (shoot / "jpg" / "a.jpg").exists() and not (shoot / "a.jpg").exists()
        assert missing == ["b.NEF"], missing
        assert [Path(u).name for u in unexpected] == ["b-DxO_DeepPRIME 3.dng"], unexpected

    print("ok - develop with PureRAW picks fresh shoots only, files JPGs into jpg/, reports drift")


if __name__ == "__main__":
    main()
