"""Cull mode's filing helpers: which files are one shot, where each version goes, the group folder.

Run: uv run python tests/test_cull.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.api.routers.cull import (base_dir, file_key, group_folder, share_copy, species_folder_of,
                                      subdir_for, versions)


def main() -> None:
    shot = ["_DSC7763.NEF", "_DSC7763.NEF.dop", "edited_DSC7763.jpg", "_DSC7763-DxO_DeepPRIME 3.dng", "_DSC7763.jpg"]
    assert {file_key(n) for n in shot} == {"dsc7763"}, [file_key(n) for n in shot]
    assert file_key("_DSC7764.NEF") != file_key("_DSC7763.NEF")
    assert file_key("Pallid Cave Crayfish (Procambarus pallidus)017001.tif").endswith("017001")

    assert [subdir_for(n) for n in ("_DSC1.NEF", "_DSC1.NEF.dop", "_DSC1.dng", "_DSC1.jpg", "edited_DSC1.tif")] == \
        ["RAW", "RAW", "dng", "", ""]

    folders = ["Birds", "Butterfly", "Hummingbird", "Snake", "Crayfish"]
    assert group_folder("Snakes", folders) == "Snake"
    assert group_folder("Butterflies", folders) == "Butterfly"
    assert group_folder("Hummingbirds", folders) == "Hummingbird"
    assert group_folder("Birds", folders) == "Birds"
    assert group_folder("Sirens", folders) == "Sirens"   # nothing like it yet: the group's own name

    assert species_folder_of("Portfolio/Crayfish/Wingtail Crayfish - Juvenile (Procambarus latipleurum)/RAW") == \
        "Portfolio/Crayfish/Wingtail Crayfish - Juvenile (Procambarus latipleurum)"
    assert species_folder_of("AA_RAW/20261017_00044") is None

    with tempfile.TemporaryDirectory() as t:
        s = Path(t) / "AA_RAW" / "20261017_00044"
        for rel in ("_DSC7763.NEF", "_DSC7763.NEF.dop", "_DSC7764.NEF", "jpg/_DSC7763.jpg", "dng/_DSC7763.dng"):
            (s / rel).parent.mkdir(parents=True, exist_ok=True)
            (s / rel).write_bytes(b"x")
        assert base_dir(s / "jpg") == s and base_dir(s) == s
        got = sorted(p.relative_to(s).as_posix() for p in versions(s / "jpg", "_DSC7763.jpg"))
        assert got == ["_DSC7763.NEF", "_DSC7763.NEF.dop", "dng/_DSC7763.dng", "jpg/_DSC7763.jpg"], got

    # Smaller copy for sharing: same pixels count, smaller file, camera info kept; a TIFF comes out upright.
    from PIL import Image
    import numpy as np
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        noise = Image.fromarray(np.random.default_rng(1).integers(0, 255, (300, 400, 3), dtype=np.uint8))
        exif = Image.Exif()
        exif[0x0110] = "NIKON Z 9"
        noise.save(t / "a.jpg", quality=97, subsampling=0, exif=exif)
        share_copy(t / "a.jpg", t / "a_share.jpg")
        with Image.open(t / "a_share.jpg") as c:
            assert c.size == (400, 300) and c.getexif()[0x0110] == "NIKON Z 9"
        assert (t / "a_share.jpg").stat().st_size < (t / "a.jpg").stat().st_size * 0.8
        (t / "a.jpg").rename(t / "a2.jpg")                  # the original isn't left locked
        tif = Image.Exif()
        tif[0x0112] = 6                                   # stored sideways, shown rotated
        noise.save(t / "b.tif", exif=tif)
        share_copy(t / "b.tif", t / "b_share.jpg")
        with Image.open(t / "b_share.jpg") as c:
            assert c.size == (300, 400)

    print("ok - a shot's versions move together; RAW/ and dng/ subfolders; group folders match singular/plural;"
          " share copies keep size and camera info")


if __name__ == "__main__":
    main()
