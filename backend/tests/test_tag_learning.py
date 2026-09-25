"""Check the per-tag classifier behind Tag Review suggestions.

Synthetic CLIP-like vectors: unit length, sharing a common direction (real CLIP
image vectors are far from isotropic), with a tag's photos pushed along one
concept direction. No live database.

Run directly: python backend/tests/test_tag_learning.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.tag_learning import THRESHOLD, fit

rng = np.random.default_rng(7)
DIM = 512
base = rng.normal(size=DIM)
base /= np.linalg.norm(base)


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def photos(n, concept=None, strength=0.35):
    v = base * 1.0 + rng.normal(scale=0.045, size=(n, DIM))
    if concept is not None:
        v = v + strength * concept
    return unit(v).astype(np.float32)


heron = unit(rng.normal(size=DIM))
# An egret looks a lot like a heron: half the same direction.
egret = unit(0.5 * heron + 0.5 * unit(rng.normal(size=DIM)))

PREVALENCE = 0.05   # herons are 5% of this library
pos = photos(40, heron)
weak = photos(600)
lookalikes = photos(40, egret)

# 1. Separable data: found, and the cross-validated numbers say so.
m = fit(pos, np.zeros((0, DIM), np.float32), weak, PREVALENCE)
assert m.coef.shape == (DIM + 1,) and m.coef.dtype == np.float32
assert m.cv_recall is not None and m.cv_recall >= 0.85, m.cv_recall
assert m.cv_agreement is not None and m.cv_agreement >= 0.9, m.cv_agreement
held_pos, held_neg = photos(50, heron), photos(500)
assert (m.scores(held_pos) >= THRESHOLD).mean() >= 0.85
assert (m.scores(held_neg) >= THRESHOLD).mean() <= 0.05

# 2. The stored coefficients apply to raw embeddings, and ranking by cosine to
#    the weight vector is ranking by score (what lets HNSW find candidates).
x = np.vstack([held_pos, held_neg]).astype(np.float64)   # float32 rounding reorders near-ties
w = m.coef[:-1].astype(np.float64)
by_score = np.argsort(-(x @ w + m.coef[-1]), kind="stable")   # the logit: probabilities saturate
by_cosine = np.argsort(-(x @ (w / np.linalg.norm(w))), kind="stable")
assert np.array_equal(by_score, by_cosine)

# 3. Rejections teach it the look-alike. Without them, egrets pass as herons;
#    with 20 rejected egrets, far fewer do, and herons are still found.
held_egrets = photos(200, egret)
before = (m.scores(held_egrets) >= THRESHOLD).mean()
m2 = fit(pos, lookalikes[:20], weak, PREVALENCE)
after = (m2.scores(held_egrets) >= THRESHOLD).mean()
assert after < before * 0.5, (before, after)
assert (m2.scores(held_pos) >= THRESHOLD).mean() >= 0.75

# 4. A tag that has just started learning (10 approved photos) is calibrated:
#    uncalibrated, it ranked well but scored nearly everything below THRESHOLD.
m4 = fit(pos[:10], lookalikes[:2], weak[:270], PREVALENCE)
assert (m4.scores(held_pos) >= THRESHOLD).mean() >= 0.9
assert (m4.scores(held_neg) >= THRESHOLD).mean() <= 0.02

# 5. Untagged herons that land in the random weak-negative sample: the final
#    model learned them as negatives, but their held-out scores still find them.
hidden = photos(8, heron)
m6 = fit(pos, np.zeros((0, DIM), np.float32), np.vstack([weak, hidden]), PREVALENCE)
assert m6.weak_scores is not None and len(m6.weak_scores) == len(weak) + 8
hidden_scores, others = m6.weak_scores[-8:], m6.weak_scores[:-8]
assert (hidden_scores >= THRESHOLD).mean() >= 0.5, hidden_scores
assert hidden_scores.min() > np.quantile(others, 0.99), (hidden_scores, np.quantile(others, 0.99))
assert (others >= THRESHOLD).mean() <= 0.01

# 6. Too few positives for cross-validation still trains, without numbers.
m5 = fit(pos[:1], np.zeros((0, DIM), np.float32), weak[:50], PREVALENCE)
assert m5.cv_agreement is None and m5.cv_recall is None

print(f"ok - tag classifier (look-alikes accepted {before:.0%} -> {after:.0%} after rejections)")
