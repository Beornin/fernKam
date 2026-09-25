"""Check the place, season and GBIF range priors behind Tag Review.

Place and season features, an ensemble where herons and egrets look the same
to the image model but live at different places and seasons, the range
table's verdicts, and the GBIF client against a fake GBIF server. No live
database, no network.

Run directly: python backend/tests/test_species_priors.py
"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam import photo_context as pc
from fernkam import species_range as sr
from fernkam.tag_learning import THRESHOLD, fit_ensemble

rng = np.random.default_rng(3)

# 1. Features.
centers = pc.fit_centers(np.array([27.0, 44.0]), np.array([-81.0, -69.0]))
assert len(centers) == 2
at = pc.place_features(np.array([27.0]), np.array([-81.0]), centers)[0]
near = np.argmax(at[:2])
assert abs(at[near] - 1) < 1e-6 and abs(at[2 + near] - 1) < 1e-6
away = pc.place_features(np.array([27.9]), np.array([-81.0]), centers)[0]   # ~100 km north
assert away[near] < 0.01 and 0.9 < away[2 + near] < 0.97, away
jan, jul = pc.season_features(np.array([1, 183]), np.array([12, 12]))
assert jan[0] > 0.99 and jul[0] < -0.99
assert sr.cell_of(-0.5, 12.7) == "-1,12" and sr.cell_of(44.2, -69.9) == "44,-70"

# 2. Herons (Florida, winter) and egrets (Maine, summer) that the image model
#    cannot tell apart. Place and season can.
DIM = 64
base, bird = rng.normal(size=DIM), rng.normal(size=DIM)


def img(n, is_bird):
    v = base + rng.normal(scale=0.1, size=(n, DIM)) + (0.6 * bird if is_bird else 0)
    return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(np.float32)


def photos(kind, n):
    if kind == "heron":
        lat, lon, doy = 27 + rng.normal(0, .3, n), -81 + rng.normal(0, .3, n), rng.integers(1, 60, n)
    elif kind == "egret":
        lat, lon, doy = 44 + rng.normal(0, .3, n), -69 + rng.normal(0, .3, n), rng.integers(150, 240, n)
    else:
        home = rng.random(n) < 0.5
        lat = np.where(home, 27, 44) + rng.normal(0, .3, n)
        lon = np.where(home, -81, -69) + rng.normal(0, .3, n)
        doy = rng.integers(1, 366, n)
    return img(n, kind != "other"), lat, lon, doy


def dataset(spec):
    parts = [photos(k, n) for k, n in spec]
    kinds = np.array([k for k, n in spec for _ in range(n)])
    x = np.vstack([p[0] for p in parts])
    lat, lon, doy = (np.concatenate([p[i] for p in parts]) for i in (1, 2, 3))
    feats = {"img": x, "place": pc.place_features(lat, lon, centers),
             "season": pc.season_features(doy, np.full(len(doy), 10))}
    return kinds, feats


kinds, xs = dataset([("heron", 40), ("egret", 10), ("other", 400), ("egret", 20)])
y = (kinds == "heron").astype(int)
is_weak = np.array([False] * 50 + [True] * 420)
ones = {k: np.ones(len(kinds), bool) for k in xs}
with_ctx = fit_ensemble(xs, ones, y, is_weak, 0.08)
img_only = fit_ensemble({"img": xs["img"]}, {"img": ones["img"]}, y, is_weak, 0.08)
w = {e.space: round(e.weight, 2) for e in with_ctx.experts}

tk, tx = dataset([("heron", 200), ("egret", 200), ("other", 1000)])
tm = {k: np.ones(len(tk), bool) for k in tx}
s_ctx, s_img = with_ctx.scores(tx, tm), img_only.scores({"img": tx["img"]}, {"img": tm["img"]})
egret_img, egret_ctx = (s_img[tk == "egret"] >= THRESHOLD).mean(), (s_ctx[tk == "egret"] >= THRESHOLD).mean()
heron_ctx = (s_ctx[tk == "heron"] >= THRESHOLD).mean()
print(f"weights {w}; egrets accepted: image only {egret_img:.2f} -> with place+season {egret_ctx:.2f}; "
      f"herons found {heron_ctx:.2f}")
assert egret_img > 0.5 and egret_ctx < 0.1 and heron_ctx >= 0.85
assert w["place"] + w["season"] > 0.3

# Ordinary photos taken at the heron's place in the heron's season are not
# herons: place and season adjust the evidence, they do not create it. (A
# reliable-negative pass that let context pick the negatives once dropped
# exactly these photos and suggested them.)
n = 300
x_other = img(n, False)
lat, lon, doy = 27 + rng.normal(0, .3, n), -81 + rng.normal(0, .3, n), rng.integers(1, 60, n)
site = {"img": x_other, "place": pc.place_features(lat, lon, centers),
        "season": pc.season_features(doy, np.full(n, 10))}
at_site = (with_ctx.scores(site, {k: np.ones(n, bool) for k in site}) >= THRESHOLD).mean()
print(f"ordinary photos at the heron's place and season accepted: {at_site:.2f}")
assert at_site <= 0.03

# The same, where it went wrong once: few approvals, weak image signal, and a
# GBIF range that says "herons" for every Florida photo. A reliable-negative
# pass that let context pick the negatives dropped the ordinary Florida-winter
# photos and suggested them (1-7.5% of them here); judged on appearance, ~0.
import datetime  # noqa: E402

D = 512
g = np.random.default_rng(99)


def u(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


B, H = u(g.normal(size=D)), u(g.normal(size=D))
E = u(0.85 * H + 0.15 * u(g.normal(size=D)))
florida = sr.RangeTable(
    {**{(f"{a},{o}", m): 300 for a in range(25, 30) for o in range(-84, -78) for m in range(1, 13)},
     **{(f"{a},{o}", 0): 3600 for a in range(25, 30) for o in range(-84, -78)},
     **{(f"{a},{o}", m): (40 if m in (6, 7, 8) else 1) for a in range(42, 47) for o in range(-72, -66) for m in range(1, 13)},
     **{(f"{a},{o}", 0): 129 for a in range(42, 47) for o in range(-72, -66)}},
    {**{(f"{a},{o}", m): 4000 for a in range(25, 30) for o in range(-84, -78) for m in range(1, 13)},
     **{(f"{a},{o}", 0): 48000 for a in range(25, 30) for o in range(-84, -78)},
     **{(f"{a},{o}", m): 2500 for a in range(42, 47) for o in range(-72, -66) for m in range(1, 13)},
     **{(f"{a},{o}", 0): 30000 for a in range(42, 47) for o in range(-72, -66)}},
    {f"{a},{o}" for a in range(25, 30) for o in range(-84, -78)} | {f"{a},{o}" for a in range(42, 47) for o in range(-72, -66)})


def hard_case(seed):
    global rng
    rng = np.random.default_rng(seed)
    spec = [("heron", 15), ("egret", 5), ("heron", 25), ("egret", 35), ("other", 200)]
    parts = []
    for kind, k in spec:
        _, la, lo, dy = photos(kind, k)
        v = B + rng.normal(scale=0.045, size=(k, D)) + (0.35 * H if kind == "heron" else 0.35 * E if kind == "egret" else 0)
        parts.append((u(v).astype(np.float32), la, lo, dy))
    kinds = np.array([kd for kd, k in spec for _ in range(k)])
    la, lo, dy = (np.concatenate([p[i] for p in parts]) for i in (1, 2, 3))
    month = [(datetime.date(2024, 1, 1) + datetime.timedelta(int(d) - 1)).month for d in dy]
    meta = {i: (float(la[i]), float(lo[i]), int(dy[i]), 10, month[i]) for i in range(len(kinds))}
    rx, rm = sr.range_vectors(florida, meta, list(range(len(kinds))))
    feats = {"img": np.vstack([p[0] for p in parts]), "place": pc.place_features(la, lo, centers),
             "season": pc.season_features(dy, np.full(len(dy), 10)), "range": rx}
    ms = {k: np.ones(len(kinds), bool) for k in feats}
    ms["range"] = rm
    yy = (np.arange(len(kinds)) < 15).astype(int)
    weak = np.arange(len(kinds)) >= 20
    ens = fit_ensemble(feats, ms, yy, weak, 0.14)
    wk = kinds[weak]
    return (ens.weak_scores[wk == "other"] >= THRESHOLD).mean(), (ens.weak_scores[wk == "heron"] >= THRESHOLD).mean()


hard = np.mean([hard_case(sd) for sd in range(4)], axis=0)
print(f"few approvals + range prior: ordinary photos suggested {hard[0]:.3f}, hidden herons found {hard[1]:.2f}")
assert hard[0] <= 0.01 and hard[1] >= 0.8

# A photo without GPS or date is judged by what it has. From the image alone a
# heron and an egret here cannot be told apart, so it is no more fooled by
# egrets than the image-only model (it is more cautious), and it still keeps
# other photos out.
no_meta = {"img": tm["img"], "place": np.zeros(len(tk), bool), "season": np.zeros(len(tk), bool)}
s_none = with_ctx.scores(tx, no_meta)
assert np.isfinite(s_none).all()
assert (s_none[tk == "egret"] >= THRESHOLD).mean() <= egret_img
assert (s_none[tk == "other"] >= THRESHOLD).mean() <= 0.02

# 3. The range table: absent, rare, fine, unknown place, too few records.
table = sr.RangeTable(
    species={("27,-81", 0): 1200, ("27,-81", 1): 300, ("27,-81", 7): 2, ("60,10", 0): 0},
    klass={("27,-81", 0): 50000, ("27,-81", 1): 4000, ("27,-81", 7): 5000, ("27,-81", 8): 10,
           ("60,10", 0): 30000, ("60,10", 1): 900},
    fetched={"27,-81", "60,10"})
assert table.status(27.5, -80.5, 1) is None                              # 300 of 4000: fine
assert table.status(27.5, -80.5, 7)["status"] == "rare"                  # 2 of 5000
assert table.status(60.4, 10.7, 1)["status"] == "absent"                 # Norway, January
assert table.status(60.4, 10.7, 1)["when"] == "Jan"
assert table.status(35.0, 139.0, 1) is None                              # not fetched
assert table.status(27.5, -80.5, 8) is None                              # 10 bird records: can't say
meta = {1: (27.5, -80.5, 10, 9, 1), 2: (60.4, 10.7, 10, 9, 1), 3: (None, None, 10, 9, 1)}
x, m = sr.range_vectors(table, meta, [1, 2, 3])
assert m.tolist() == [True, True, False] and x[1, 3] == 1.0 and x[0, 0] > x[1, 0]


# 4. GBIF client: exact query parameters, facet parsing, name search merging.
seen = []


class FakeGBIF(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        seen.append((u.path, q))
        if u.path == "/v1/species/match":
            body = {"usageKey": 2480446, "scientificName": "Ardea herodias Linnaeus, 1758",
                    "canonicalName": "Ardea herodias", "rank": "SPECIES", "status": "ACCEPTED",
                    "matchType": "EXACT", "classKey": 212, "class": "Aves", "family": "Ardeidae"} \
                if q["name"] == "Ardea herodias" else {"matchType": "NONE"}
        elif u.path == "/v1/species/search":
            body = {"results": [
                {"key": 2480446, "nubKey": 2480446, "canonicalName": "Ardea herodias", "rank": "SPECIES",
                 "taxonomicStatus": "ACCEPTED", "classKey": 212, "class": "Aves", "family": "Ardeidae",
                 "vernacularNames": [{"vernacularName": "Garza azulada", "language": "spa"},
                                     {"vernacularName": "Great Blue Heron", "language": "eng"}]},
                {"key": 212, "canonicalName": "Aves", "rank": "CLASS", "classKey": 212},
                {"key": 2480445, "canonicalName": "Ardea", "rank": "GENUS", "taxonomicStatus": "ACCEPTED",
                 "classKey": 212, "class": "Aves"}]}
        elif u.path == "/v1/occurrence/search":
            body = {"count": 1500, "results": [], "facets": [
                {"field": "MONTH", "counts": [{"name": "1", "count": 400}, {"name": "12", "count": 380}]}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = HTTPServer(("127.0.0.1", 0), FakeGBIF)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["GBIF_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
from fernkam.config import get_settings  # noqa: E402
get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None


async def gbif():
    hits = await sr.search("great blue heron")
    assert [h["rank"] for h in hits] == ["SPECIES", "GENUS"], hits          # the class is not linkable
    assert hits[0]["common_name"] == "Great Blue Heron" and hits[0]["class_key"] == 212
    exact = await sr.search("Ardea herodias")
    assert exact[0]["taxon_key"] == 2480446 and len([h for h in exact if h["taxon_key"] == 2480446]) == 1
    async with sr._client() as c:
        counts = await sr.fetch_box(c, 2480446, "27,-81")
    assert counts == {0: 1500, 1: 400, 12: 380}
    path, q = seen[-1]
    assert path == "/v1/occurrence/search"
    assert q["decimalLatitude"] == "26,29" and q["decimalLongitude"] == "-82,-79"
    assert q["facet"] == "month" and q["limit"] == "0" and q["occurrenceStatus"] == "PRESENT"
    assert q["facetLimit"] == "12" and q["hasGeospatialIssue"] == "false"
    async with sr._client() as c:
        await sr.fetch_box(c, 1, "89,179")                                   # edges clamp
    assert seen[-1][1]["decimalLatitude"] == "88,90" and seen[-1][1]["decimalLongitude"] == "178,180"

asyncio.run(gbif())

print(f"ok - species priors (egrets that fool the image model: {egret_img:.0%} -> {egret_ctx:.0%} "
      f"with place and season; GBIF range verdicts; GBIF client)")
