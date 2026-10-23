"""Camera fact tags: Camera › Nikon › Z9, Camera › Canon › 40D, from each photo's own EXIF make and model.

Facts, never learned (tags.is_fact), kept in step at every Learn all tags alongside species status.
Unlike other tags they are not flagged for write-back: the file already says which camera took it, and
flagging would rewrite every original in the library. A file written for another reason carries them.
"""
from __future__ import annotations

import re

from sqlalchemy import text

_CAMERA_WORDS = re.compile(r"\s+(zoom\s+)?digital\s+camera$|\s+camera$", re.I)


def _word(w: str) -> str:
    """NIKON -> Nikon, COOLPIX -> Coolpix; DJI, LG, VTech, iPhone stay as written."""
    return w.capitalize() if w.isalpha() and w.isupper() and len(w) > 3 else w


def names(make: str | None, model: str | None) -> tuple[str, str] | None:
    """('NIKON CORPORATION', 'NIKON Z 9') -> ('Nikon', 'Z9'); ('Canon', 'Canon EOS 40D') -> ('Canon', '40D');
    ('EASTMAN KODAK COMPANY', 'KODAK EASYSHARE M1063 DIGITAL CAMERA') -> ('Kodak', 'Easyshare M1063').
    The model is '' when only the maker is known."""
    make, model = (make or "").strip(), (model or "").strip()
    if not make and not model:
        return None
    first = model.split()[0] if model else ""
    # The model usually starts with the brand ("Canon EOS 40D"); the make often has company words.
    brand = first if first and first.lower() in make.lower().split() else (make.split() or [first])[0]
    rest = model[len(first):].strip() if first.lower() == brand.lower() else model
    rest = re.sub(r"^EOS\s+", "", _CAMERA_WORDS.sub("", rest))   # Canon's series: the EOS 40D is the 40D
    rest = " ".join(_word(w) for w in rest.split())
    if len(rest) <= 5:
        rest = rest.replace(" ", "")                               # "Z 9" -> "Z9"
    return _word(brand), rest


def _label(name: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return "_" + label if label[:1].isdigit() else label


async def _tag(db, name: str, parent: int | None, path: str) -> int:
    tid = (await db.execute(text("SELECT id FROM tags WHERE path = CAST(:p AS ltree)"), {"p": path})).scalar()
    if tid is None:
        tid = (await db.execute(text("""INSERT INTO tags (name, path, parent_id, is_person, is_fact)
            VALUES (:n, CAST(:p AS ltree), :r, false, true) RETURNING id"""), {"n": name, "p": path, "r": parent})).scalar_one()
    return tid


async def apply(db) -> int:
    """Put each photo's camera tag on it (and take stale ones off). Returns links changed. Does not commit."""
    root = await _tag(db, "Camera", None, "Camera")
    want: dict[int, int] = {}
    for cid, make, model in (await db.execute(text("SELECT id, make, model FROM cameras"))).all():
        if not (n := names(make, model)):
            continue
        brand, model_name = n
        b = await _tag(db, brand, root, f"Camera.{_label(brand)}")
        want[cid] = await _tag(db, model_name, b, f"Camera.{_label(brand)}.{_label(model_name)}") if model_name else b
    p = {"c": list(want), "t": list(want.values())}
    pairs = "SELECT p.id, m.t FROM photos p JOIN unnest(CAST(:c AS int[]), CAST(:t AS int[])) AS m(c, t) ON p.camera_id = m.c"
    gone = (await db.execute(text(f"""
        DELETE FROM photo_tags x USING tags t WHERE x.tag_id = t.id AND t.path <@ 'Camera'
          AND NOT EXISTS (SELECT 1 FROM ({pairs}) w WHERE w.id = x.photo_id AND w.t = x.tag_id)
        RETURNING x.photo_id"""), p)).scalars().all()
    added = (await db.execute(text(f"""
        INSERT INTO photo_tags (photo_id, tag_id, verified_at) SELECT id, t, now() FROM ({pairs}) w
        ON CONFLICT (photo_id, tag_id) DO NOTHING RETURNING photo_id"""), p)).scalars().all()
    return len(gone) + len(added)
