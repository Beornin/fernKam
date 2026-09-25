"""Check how Tag Review combines several image models per tag.

Synthetic vectors in three "models": a general one where herons and egrets
look nearly alike (like CLIP), a species one that tells them apart (like
BioCLIP), and one that carries no signal at all. The stacker should lean on
the species model, ignore the noise, and still work for photos the species
model has not indexed yet. No live database.

Run directly: python backend/tests/test_tag_ensemble.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.tag_learning import THRESHOLD, fit_ensemble

rng = np.random.default_rng(11)


def unit(v):
    return (v / np.linalg.norm(v, axis=-1, keepdims=True)).astype(np.float32)


class Model:
    def __init__(self, dim, egret_overlap):
        self.base = unit(rng.normal(size=dim))
        self.heron = unit(rng.normal(size=dim))
        self.egret = unit(egret_overlap * self.heron + (1 - egret_overlap) * unit(rng.normal(size=dim)))
        self.dim = dim

    def photos(self, kind, n):
        v = self.base + rng.normal(scale=0.045, size=(n, self.dim))
        if kind == "heron":
            v = v + 0.35 * self.heron
        elif kind == "egret":
            v = v + 0.35 * self.egret
        return unit(v)


general = Model(512, egret_overlap=0.85)   # CLIP-like: egrets look like herons
bio = Model(768, egret_overlap=0.1)        # BioCLIP-like: clearly different
noise_dim = 256


def sample(kinds_counts):
    kinds = [k for k, n in kinds_counts for _ in range(n)]
    xs = {"general": np.vstack([general.photos(k, 1) for k in kinds]),
          "bio": np.vstack([bio.photos(k, 1) for k in kinds]),
          "noise": unit(rng.normal(size=(len(kinds), noise_dim)))}
    return kinds, xs


# Training: 40 approved herons, 20 rejected egrets, 600 random photos (weak),
# some of which are egrets nobody has rejected yet.
kinds, xs = sample([("heron", 40), ("egret", 20), ("other", 570), ("egret", 30)])
y = np.array([k == "heron" for k in kinds], int)
is_weak = np.array([False] * 60 + [True] * 600)
full = {k: np.ones(len(kinds), bool) for k in xs}
ens = fit_ensemble(xs, full, y, is_weak, prevalence=0.05)
w = {e.space: e.weight for e in ens.experts}
print("weights:", {k: round(v, 2) for k, v in w.items()},
      "| per model recall:", {e.space: e.cv_recall for e in ens.experts})

# 1. It leans on the model that can tell the species apart, and ignores noise.
assert all(v >= 0 for v in w.values())
assert w["bio"] > w["general"] and w["bio"] > 5 * w["noise"], w
assert ens.cv_recall is not None and ens.cv_agreement is not None and ens.weak_scores is not None

# 2. Held out: herons found, egrets and others kept out, better than the
#    general model alone on egrets.
test_kinds, tx = sample([("heron", 200), ("egret", 200), ("other", 2000)])
tk = np.array(test_kinds)
s = ens.scores(tx)
heron_recall = (s[tk == "heron"] >= THRESHOLD).mean()
egret_fa = (s[tk == "egret"] >= THRESHOLD).mean()
other_fa = (s[tk == "other"] >= THRESHOLD).mean()
only_general = fit_ensemble({"general": xs["general"]}, {"general": full["general"]}, y, is_weak, 0.05)
general_egret_fa = (only_general.scores({"general": tx["general"]}) [tk == "egret"] >= THRESHOLD).mean()
print(f"ensemble: heron recall {heron_recall:.2f}, egrets accepted {egret_fa:.2f} "
      f"(general model alone {general_egret_fa:.2f}), others accepted {other_fa:.3f}")
assert heron_recall >= 0.9 and other_fa <= 0.01
assert egret_fa < 0.1 and egret_fa < general_egret_fa / 2

# 3. Photos the species model has not indexed yet: its vote is simply missing.
half = {k: v.copy() for k, v in full.items()}
half["bio"][rng.random(len(kinds)) < 0.5] = False
ens_half = fit_ensemble({k: np.where(half[k][:, None], v, 0) for k, v in xs.items()}, half, y, is_weak, 0.05)
test_mask = {"general": np.ones(len(tk), bool), "noise": np.ones(len(tk), bool),
             "bio": np.zeros(len(tk), bool)}
s_nobio = ens_half.scores(tx, test_mask)
assert np.isfinite(s_nobio).all()
# Those photos are judged as well as a general-model-only tag would judge them.
g_only = only_general.scores({"general": tx["general"]})
nobio_recall = (s_nobio[tk == "heron"] >= THRESHOLD).mean()
general_recall = (g_only[tk == "heron"] >= THRESHOLD).mean()
print(f"without species vectors: heron recall {nobio_recall:.2f} (general model alone {general_recall:.2f})")
assert nobio_recall >= general_recall - 0.1
assert (s_nobio[tk == "other"] >= THRESHOLD).mean() <= 0.02
s_bio = ens_half.scores(tx)
assert (s_bio[tk == "egret"] >= THRESHOLD).mean() < (s_nobio[tk == "egret"] >= THRESHOLD).mean()

# 4. A model that has only seen negatives is left out, not crashed on.
only_neg = {k: v.copy() for k, v in full.items()}
only_neg["bio"][:40] = False
ens_neg = fit_ensemble(xs, only_neg, y, is_weak, 0.05)
assert "bio" not in {e.space for e in ens_neg.experts}

# 5. A small library, where the random sample holds most untagged herons: the
#    second pass (reliable negatives only) still finds them, look-alikes stay out.
kinds, xs = sample([("heron", 40), ("egret", 5), ("heron", 20), ("egret", 35), ("other", 212)])
kk = np.array(kinds)
y = np.array([1] * 40 + [0] * (len(kinds) - 40))
is_weak = np.array([False] * 45 + [True] * (len(kinds) - 45))
small = fit_ensemble(xs, {k: np.ones(len(kinds), bool) for k in xs}, y, is_weak, 40 / 312)
hidden_found = (small.weak_scores[kk[45:] == "heron"] >= THRESHOLD).mean()
hidden_egrets = (small.weak_scores[kk[45:] == "egret"] >= THRESHOLD).mean()
print(f"small library: hidden herons found {hidden_found:.2f}, hidden egrets {hidden_egrets:.2f}")
assert hidden_found >= 0.85 and hidden_egrets <= 0.15

print(f"ok - tag ensemble (species model weighted {w['bio']:.2f} vs general {w['general']:.2f}; "
      f"egrets accepted {general_egret_fa:.0%} -> {egret_fa:.0%})")
