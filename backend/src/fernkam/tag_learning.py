"""Tag models: learn each tag from the tags the user has checked.

Tags work like faces. A face only counts as known once the user confirms it, and
the matcher learns from confirmed faces. Here, every tag starts unverified: it
came from a file, from digiKam or from a workflow, and nobody has checked it.
The user approves or rejects tags on the Tag Review page, and only those
decisions train the models:

  positives   approved links to the tag or to one of its descendants
              (a photo approved as Birds.Heron is a positive for Birds)
  negatives   rejections of the tag or of an ancestor (not a bird, so not a
              heron), plus a random sample of other photos as weak negatives
  ignored     unverified tags. They are neither positives nor negatives.

Several image models see each photo (CLIP, and whichever of SigLIP 2 and
BioCLIP 2 are indexed; see embed_models). Each is an "expert": per tag, one
logistic regression on that model's vectors, calibrated on its
cross-validation output (Platt scaling). A stacker then learns, per tag, how
much to trust each expert: non-negative weights fitted on the experts'
held-out scores. A wildlife model ends up carrying species tags and a general
model scenes, because that is what the user's decisions reward. There is one
stacker per combination of models, so a photo one model has not indexed yet
is judged by a combiner trained for the models it does have.

Where and when a photo was taken are experts too (photo_context): "place"
and "season" learn from the user's approvals where and when a tag turns up,
and "range" (species_range) brings GBIF's records of a linked species, so a
heron in Norway in January is doubted even on a first trip there. They join
the same stacking, so each tag learns how much its context matters: a lot
for a migrant species, nothing for "Sunset". They re-score candidates; only
image models can find them.

Vectors are unit length, so ranking photos by one expert's w·x is ranking by
cosine to w, and each model's HNSW index finds its best candidates directly.
Candidates from every model are pooled and scored by the whole ensemble.

A random photo used as a weak negative might really be the tag, just
untagged. The final model has learned to score it down, so such photos are
judged by their cross-validation score instead, from models that never saw
them (PU bagging).

Scores are calibrated as if the tag were on half of all photos. That fits
an unverified tag (being tagged is already evidence), but not a search of
the whole library, where most photos are not the tag. For suggestions the
tag's prevalence is added back in (Bayes' rule for a prior shift), so a
suggestion means "more likely than not".

Before a tag has MIN_POSITIVES approved photos, find_by_name() searches for it
by its name with the models' text towers, to get the first approvals quickly.

Suggestions go into tag_suggestions for review. Accepting or rejecting them
adds labels, and after RETRAIN_EVERY new labels the tag is retrained. That is
the feedback loop. A local vision model can double-check them (vision_check);
its answers are shown and measured, never used as labels.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

import numpy as np
from sqlalchemy import text

from fernkam import embed_index as ei
from fernkam import photo_context as pc
from fernkam import species_range as sr

logger = logging.getLogger(__name__)

MIN_POSITIVES = 8            # approved photos before a tag starts learning
RETRAIN_EVERY = 10           # new approvals/rejections before a tag retrains
MAX_POSITIVES = 3000         # sampled above this; keeps training under a few seconds
WEAK_NEG_PER_POSITIVE = 10
WEAK_NEG_MIN, WEAK_NEG_MAX = 500, 4000
WEAK_WEIGHT = 0.25           # a random photo is only probably not this tag
THRESHOLD = 0.5
MAX_SUGGESTIONS = 300        # per tag, best first
MIN_PREVALENCE = 0.002       # floor for the prior of a newly used tag
CONTEXT_EXPERTS = {"place", "season", "range"}   # re-score only; never decide who is a negative
NAME_SUGGESTIONS = 60        # find_by_name: photos per tag
STACK_L2 = 1e-2


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _weights(y: np.ndarray, weak: np.ndarray) -> np.ndarray:
    """Weak negatives count WEAK_WEIGHT, then positives and negatives are
    balanced so a rare tag is not drowned by the negative sample. Scaled to
    average 1, so regularisation means the same for small and large tags."""
    w = np.where(weak, WEAK_WEIGHT, 1.0)
    pos, neg = w[y == 1].sum(), w[y == 0].sum()
    w[y == 1] *= neg / max(pos, 1e-9)
    return w * len(w) / w.sum()


def _train(x: np.ndarray, y: np.ndarray, weak: np.ndarray) -> np.ndarray:
    from sklearn.linear_model import LogisticRegression

    # Standardise per dimension for a well-conditioned fit, then fold the
    # scaling back in so the stored model applies to raw embeddings.
    mu, sd = x.mean(axis=0), x.std(axis=0) + 1e-6
    clf = LogisticRegression(C=0.1, max_iter=2000)
    clf.fit((x - mu) / sd, y, sample_weight=_weights(y, weak))
    w = clf.coef_[0] / sd
    b = clf.intercept_[0] - float(w @ mu)
    return np.append(w, b).astype(np.float32)


def _calibrate(z: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    """Platt scaling: fit p = sigmoid(a*z + b) to held-out scores. With few
    positives the regularised model ranks well but is under-confident; this
    makes THRESHOLD mean the same thing for every tag."""
    from sklearn.linear_model import LogisticRegression

    cal = LogisticRegression(C=1e4, max_iter=1000).fit(z.reshape(-1, 1), y, sample_weight=w)
    a, b = float(cal.coef_[0][0]), float(cal.intercept_[0])
    return (a, b) if a > 0 else (1.0, 0.0)   # no signal: leave it uncalibrated


def _stack(z: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, float]:
    """Non-negative weights for the experts' calibrated scores (plus a bias),
    by weighted log-loss. Non-negative: an expert can be ignored, never used
    backwards."""
    from scipy.optimize import minimize

    k = z.shape[1]
    sign, total = 2 * y - 1, w.sum()

    def loss(p):
        a, b = p[:k], p[k]
        s = z @ a + b
        g = w * (_sigmoid(s) - y) / total
        value = float((w * np.logaddexp(0, -sign * s)).sum() / total + STACK_L2 * (a @ a))
        return value, np.append(z.T @ g + 2 * STACK_L2 * a, g.sum())

    r = minimize(loss, np.append(np.full(k, 1.0 / k), 0.0), jac=True, method="L-BFGS-B",
                 bounds=[(0, None)] * k + [(None, None)])
    return r.x[:k], float(r.x[k])


@dataclass
class Expert:
    space: str
    coef: np.ndarray                  # calibrated: balanced logit = x @ coef[:-1] + coef[-1]
    weight: float = 1.0               # stacking weight for this tag
    cv_recall: Optional[float] = None
    cv_agreement: Optional[float] = None
    photos: int = 0                   # training photos that had this model's vector


Stackers = dict[tuple[int, ...], tuple[np.ndarray, float]]


def _fit_stackers(z: np.ndarray, avail: np.ndarray, y: np.ndarray, is_weak: np.ndarray) -> Stackers:
    """One stacker per combination of experts: a photo only some models have
    seen is judged by a combiner trained for exactly those models, so a
    missing wildlife vector means leaning on the general model, not a lower
    score."""
    k = z.shape[1]
    out: Stackers = {}
    for size in range(1, k + 1):
        for combo in combinations(range(k), size):
            rows = avail[:, combo].all(axis=1)
            if rows.sum() < 10 or len(set(y[rows])) < 2:
                continue
            out[combo] = _stack(z[rows][:, combo], y[rows], _weights(y[rows], is_weak[rows]))
    return out


def _apply_stackers(st: Stackers, z: np.ndarray, avail: np.ndarray) -> np.ndarray:
    """Balanced logits, each row by the stacker for the models it has. A
    combination without its own stacker averages its experts; no model at
    all is 0 (cannot tell)."""
    out = np.zeros(len(z))
    patterns = {}
    for i, row in enumerate(avail):
        patterns.setdefault(tuple(np.flatnonzero(row)), []).append(i)
    for combo, rows in patterns.items():
        if not combo:
            continue
        zz = z[np.ix_(rows, combo)]
        if combo in st:
            a, b = st[combo]
            out[rows] = zz @ a + b
        else:
            out[rows] = zz.mean(axis=1)
    return out


@dataclass
class Ensemble:
    experts: list[Expert]
    stackers: Stackers                # by combination of expert indices
    shift: float                      # log-odds of the tag's prevalence, for library-wide scores
    cv_agreement: Optional[float] = None
    cv_recall: Optional[float] = None
    weak_scores: Optional[np.ndarray] = None   # held-out library-wide score of each weak negative
    fold_count: int = field(default=0)
    oof_z: Optional[np.ndarray] = field(default=None, repr=False)      # experts' held-out logits
    oof_avail: Optional[np.ndarray] = field(default=None, repr=False)

    def logits(self, vecs: dict[str, np.ndarray], masks: Optional[dict[str, np.ndarray]] = None) -> np.ndarray:
        """Balanced logits for n photos. vecs[space] is (n, dim); masks[space]
        marks rows that have that model's vector."""
        n = len(next(iter(vecs.values())))
        z = np.zeros((n, len(self.experts)))
        avail = np.zeros((n, len(self.experts)), bool)
        for j, e in enumerate(self.experts):
            if e.space not in vecs:
                continue
            z[:, j] = vecs[e.space] @ e.coef[:-1] + e.coef[-1]
            avail[:, j] = masks[e.space] if masks is not None and e.space in masks else True
        return _apply_stackers(self.stackers, z, avail)

    def scores(self, vecs, masks=None, library: bool = True) -> np.ndarray:
        """library=True: chance a photo from the library has the tag.
        library=False: how much a photo already tagged with it looks right."""
        return _sigmoid(self.logits(vecs, masks) + (self.shift if library else 0.0))

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez(buf, shift=self.shift, spaces=np.array([e.space for e in self.experts]),
                 **{f"coef_{e.space}": e.coef for e in self.experts},
                 **{"stack_" + "_".join(map(str, c)): np.append(a, b) for c, (a, b) in self.stackers.items()})
        return buf.getvalue()


def fit_ensemble(xs: dict[str, np.ndarray], masks: dict[str, np.ndarray], y: np.ndarray,
                 is_weak: np.ndarray, prevalence: float) -> Ensemble:
    """Two passes. The first flags weak negatives whose held-out score says
    "more like the tag than not"; they are probably untagged positives, so the
    second pass leaves them out of training (keeping only reliable negatives,
    the usual PU-learning refinement) and scores them with a model that never
    saw them. On a small library, where the random sample holds many of the
    untagged photos of a tag, this took recall of those photos from 65% to 94%
    in simulation without letting look-alikes in.
    """
    ens = _fit_once(xs, masks, y, is_weak, prevalence)
    if ens.weak_scores is None:
        return ens
    widx = np.flatnonzero(is_weak)
    # Judged on appearance alone: where and when adjust the evidence, but they
    # must not decide which photos stop counting as negatives. Otherwise every
    # photo taken at the heron's marsh in season looks like a hidden heron,
    # drops out, and nothing is left to teach that the place alone is not enough.
    ctx = [j for j, e in enumerate(ens.experts) if e.space in CONTEXT_EXPERTS]
    if ctx and len(ctx) < len(ens.experts) and ens.oof_z is not None:
        avail = ens.oof_avail.copy()
        avail[:, ctx] = False
        balanced = _apply_stackers(ens.stackers, ens.oof_z, avail)[widx]
    else:
        ws = np.clip(ens.weak_scores, 1e-9, 1 - 1e-9)
        balanced = np.log(ws / (1 - ws)) - ens.shift
    likely = widx[balanced > 0]
    if not len(likely):
        return ens
    keep = np.ones(len(y), bool)
    keep[likely] = False
    if (y[keep] == 0).sum() == 0:
        return ens
    ens2 = _fit_once({k: v[keep] for k, v in xs.items()}, {k: m[keep] for k, m in masks.items()},
                     y[keep], is_weak[keep], prevalence)
    if ens2.weak_scores is None:
        return ens
    scores = np.empty(len(widx))
    slot = {r: i for i, r in enumerate(widx)}
    for r, sc in zip([r for r in widx if keep[r]], ens2.weak_scores):
        scores[slot[r]] = sc
    held = ens2.scores({k: v[likely] for k, v in xs.items()}, {k: m[likely] for k, m in masks.items()})
    for r, sc in zip(likely, held):
        scores[slot[r]] = sc
    ens2.weak_scores = scores
    return ens2


def _fit_once(xs: dict[str, np.ndarray], masks: dict[str, np.ndarray], y: np.ndarray,
              is_weak: np.ndarray, prevalence: float) -> Ensemble:
    """Train one expert per image model and stack them. xs[space] is (n, dim)
    with zero rows where masks[space] is False (no vector from that model).

    Returned numbers are cross-validated on the same folds:
    cv_recall     share of approved photos it would suggest (library-wide
                  score, prevalence included);
    cv_agreement  share of the user's own decisions (approved and rejected) it
                  gets right, judged like an unverified tag (no prevalence).
    Weak negatives are left out of both: some are untagged positives, so they
    cannot say whether the model is wrong. Precision on new photos is measured
    by the review itself (hit rate).
    """
    y = np.asarray(y, dtype=int)
    n = len(y)
    prevalence = min(max(prevalence, MIN_PREVALENCE), 0.5)
    shift = float(np.log(prevalence / (1 - prevalence)))
    judged = ~is_weak
    n_folds = min(5, int((y == 1).sum()))
    folds = []
    if n_folds >= 2:
        from sklearn.model_selection import StratifiedKFold
        folds = list(StratifiedKFold(n_folds, shuffle=True, random_state=0).split(np.zeros(n), y))

    experts: list[Expert] = []
    z_cols: list[np.ndarray] = []
    got_cols: list[np.ndarray] = []
    for sp, x in xs.items():
        m = masks[sp]
        rows = np.flatnonzero(m)
        if (y[rows] == 1).sum() < 1 or (y[rows] == 0).sum() < 1:
            continue   # this model has not seen both kinds yet
        oof, got = np.zeros(n), np.zeros(n, bool)
        for tr, te in folds:
            tr_s, te_s = tr[m[tr]], te[m[te]]
            if len(te_s) == 0 or (y[tr_s] == 1).sum() < 1 or (y[tr_s] == 0).sum() < 1:
                continue
            c = _train(x[tr_s], y[tr_s], is_weak[tr_s])
            oof[te_s] = x[te_s] @ c[:-1] + c[-1]
            got[te_s] = True
        a, b = 1.0, 0.0
        if got.any() and len(set(y[got])) == 2:
            a, b = _calibrate(oof[got], y[got], _weights(y[got], is_weak[got]))
        z = np.where(got, a * oof + b, 0.0)
        c = _train(x[rows], y[rows], is_weak[rows])
        e = Expert(sp, np.append(c[:-1] * a, c[-1] * a + b).astype(np.float32), photos=len(rows))
        if got.any():
            pos_rows = got & (y == 1)
            if pos_rows.any():
                e.cv_recall = float((_sigmoid(z[pos_rows] + shift) >= THRESHOLD).mean())
            jr = got & judged
            if jr.any():
                e.cv_agreement = float(((_sigmoid(z[jr]) >= THRESHOLD) == (y[jr] == 1)).mean())
        experts.append(e)
        z_cols.append(z)
        got_cols.append(got)

    if not experts:
        raise ValueError("no image model has both approved and other photos")
    k = len(experts)
    identity: Stackers = {(0,): (np.ones(1), 0.0)}
    ens = Ensemble(experts, identity, shift, fold_count=len(folds))
    if not folds:
        ens.stackers = {tuple(range(k)): (np.full(k, 1.0 / k), 0.0)}
        for e in experts:
            e.weight = 1.0 / k
        return ens

    z = np.column_stack(z_cols)
    avail = np.column_stack(got_cols)
    ens.oof_z, ens.oof_avail = z, avail
    if k == 1:
        ens_oof = np.where(avail[:, 0], z[:, 0], 0.0)
    else:
        ens.stackers = _fit_stackers(z, avail, y, is_weak)
        full = ens.stackers.get(tuple(range(k)))
        for j, e in enumerate(experts):
            e.weight = float(full[0][j]) if full else 1.0 / k
        ens_oof = np.zeros(n)   # the stackers, cross-validated too
        for tr, te in folds:
            ens_oof[te] = _apply_stackers(_fit_stackers(z[tr], avail[tr], y[tr], is_weak[tr]),
                                          z[te], avail[te])
    library = _sigmoid(ens_oof + shift)
    ens.weak_scores = library[is_weak]
    ens.cv_recall = float((library[y == 1] >= THRESHOLD).mean())
    ens.cv_agreement = float(((_sigmoid(ens_oof[judged]) >= THRESHOLD) == (y[judged] == 1)).mean())
    return ens


@dataclass
class Fit:
    """One model's view, as the single-model API returns it."""
    coef: np.ndarray
    shift: float
    cv_agreement: Optional[float]
    cv_recall: Optional[float]
    weak_scores: Optional[np.ndarray] = None

    def scores(self, x: np.ndarray, library: bool = True) -> np.ndarray:
        return _sigmoid(x @ self.coef[:-1] + self.coef[-1] + (self.shift if library else 0.0))


def fit(pos: np.ndarray, neg: np.ndarray, weak: np.ndarray, prevalence: float) -> Fit:
    """fit_ensemble with a single image model."""
    x = np.vstack([pos, neg, weak]).astype(np.float32)
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg) + len(weak))]).astype(int)
    is_weak = np.concatenate([np.zeros(len(pos) + len(neg), bool), np.ones(len(weak), bool)])
    ens = fit_ensemble({"x": x}, {"x": np.ones(len(y), bool)}, y, is_weak, prevalence)
    e = ens.experts[0]
    return Fit(e.coef, ens.shift, ens.cv_agreement, ens.cv_recall, ens.weak_scores)


# ── database side ────────────────────────────────────────────────────────────

# A tag's positives: approved links to it or a descendant.
_POSITIVE_PHOTOS = """
    SELECT DISTINCT pt.photo_id FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
    WHERE t.path <@ CAST(:path AS ltree) AND pt.verified_at IS NOT NULL
"""
# Its negatives: rejected for it or for an ancestor, unless also a positive.
_NEGATIVE_PHOTOS = f"""
    SELECT DISTINCT r.photo_id FROM tag_rejections r JOIN tags t ON t.id = r.tag_id
    WHERE CAST(:path AS ltree) <@ t.path
      AND r.photo_id NOT IN ({_POSITIVE_PHOTOS})
"""
# Photos some image model has seen.
_HAS_VECTOR = ("(p.embedding_v IS NOT NULL OR EXISTS "
               "(SELECT 1 FROM photo_embeddings e WHERE e.photo_id = p.id))")
# Photos that may be suggested the tag: not tagged with it or a descendant
# (a photo with only an ancestor can be suggested the more specific tag), and
# not rejected for it.
_SUGGESTABLE = """
    NOT EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                WHERE pt.photo_id = p.id AND t.path <@ CAST(:path AS ltree))
    AND NOT EXISTS (SELECT 1 FROM tag_rejections r WHERE r.photo_id = p.id AND r.tag_id = :tid)
"""


async def label_counts(db, path: str) -> tuple[int, int]:
    row = (await db.execute(text(f"""
        SELECT (SELECT count(*) FROM ({_POSITIVE_PHOTOS}) a),
               (SELECT count(*) FROM ({_NEGATIVE_PHOTOS}) b)
    """), {"path": path})).one()
    return int(row[0]), int(row[1])


async def _supports_iterative_scan(db) -> bool:
    v = (await db.execute(text(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"))).scalar()
    try:
        return tuple(int(p) for p in (v or "0").split(".")[:2]) >= (0, 8)
    except ValueError:
        return False


async def _hnsw_settings(db, k: int) -> None:
    if await _supports_iterative_scan(db):
        await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await db.execute(text(f"SET LOCAL hnsw.ef_search = {max(k * 2, 40)}"))


async def _ids(db, sql: str, params: dict) -> list[int]:
    return [r[0] for r in (await db.execute(text(sql), params)).all()]


@dataclass
class Context:
    """What the context experts need for one tag: the library's place
    centres and the linked species' GBIF range table."""
    centers: Optional[np.ndarray] = None
    range_table: Optional["sr.RangeTable"] = None


async def _context_spaces(db, tag_id: int) -> tuple[list, Context]:
    """Place and season from the library's own GPS and dates; range when the
    tag is linked to a GBIF species with data fetched."""
    ctx = Context(await pc.library_centers(db), await sr.RangeTable.load(db, tag_id))
    spaces: list = []
    if ctx.centers is not None:
        spaces.append(pc.place_space(len(ctx.centers)))
    if (await db.execute(text("SELECT EXISTS (SELECT 1 FROM photos WHERE taken_at IS NOT NULL)"))).scalar():
        spaces.append(pc.SEASON)
    if ctx.range_table is not None:
        spaces.append(sr.RANGE)
    return spaces, ctx


async def _matrix(db, spaces: list, ids: list[int], ctx: Optional[Context] = None) -> tuple[dict, dict]:
    """Vectors of ids in every space: ({space: (n, dim)}, {space: has-vector mask})."""
    xs, masks = {}, {}
    meta = None
    for sp in spaces:
        if isinstance(sp, pc.ContextSpace):
            if meta is None:
                meta = await pc.photo_meta(db, ids)
            if sp.key == "range":
                xs[sp.key], masks[sp.key] = sr.range_vectors(ctx.range_table if ctx else None, meta, ids)
            else:
                xs[sp.key], masks[sp.key] = pc.context_vectors(sp, meta, ids, ctx.centers if ctx else None)
            continue
        got = await ei.fetch_vectors(db, sp, ids)
        x = np.zeros((len(ids), sp.dim), np.float32)
        m = np.zeros(len(ids), bool)
        for i, pid in enumerate(ids):
            v = got.get(pid)
            if v is not None and len(v) == sp.dim:
                x[i], m[i] = v, True
        xs[sp.key], masks[sp.key] = x, m
    return xs, masks


async def train_tag(db, tag_id: int) -> Optional[dict]:
    """Train one tag with every indexed image model, score its unverified
    links and refresh its suggestions. Returns the model's stats, or None when
    the tag has too few approved photos with vectors. Commits."""
    tag = (await db.execute(text(
        "SELECT path::text, is_person FROM tags WHERE id = :t"), {"t": tag_id})).first()
    if not tag or tag.is_person:
        return None
    image_spaces = await ei.active_spaces(db)
    if not image_spaces:
        return None
    context_spaces, ctx = await _context_spaces(db, tag_id)
    spaces = image_spaces + context_spaces
    path = tag.path
    p = {"path": path, "tid": tag_id}

    pos_ids = await _ids(db, f"""
        SELECT p.id FROM photos p WHERE p.id IN ({_POSITIVE_PHOTOS}) AND p.status = 1 AND {_HAS_VECTOR}
        ORDER BY random() LIMIT {MAX_POSITIVES}""", p)
    if len(pos_ids) < MIN_POSITIVES:
        return None
    neg_ids = await _ids(db, f"""
        SELECT p.id FROM photos p WHERE p.id IN ({_NEGATIVE_PHOTOS}) AND {_HAS_VECTOR}""", p)
    n_weak = min(max(len(pos_ids) * WEAK_NEG_PER_POSITIVE, WEAK_NEG_MIN), WEAK_NEG_MAX)
    # Weak negatives: any photo not tagged with this tag or a descendant, in
    # any state. An unverified link might be right, so it is left out.
    weak_ids = await _ids(db, f"""
        SELECT p.id FROM photos p WHERE p.status = 1 AND {_HAS_VECTOR} AND {_SUGGESTABLE}
        ORDER BY random() LIMIT {n_weak}""", p)
    if not neg_ids and not weak_ids:
        return None

    ids = pos_ids + neg_ids + weak_ids
    xs, masks = await _matrix(db, spaces, ids, ctx)
    y = np.array([1] * len(pos_ids) + [0] * (len(neg_ids) + len(weak_ids)))
    is_weak = np.array([False] * (len(pos_ids) + len(neg_ids)) + [True] * len(weak_ids))

    # How common the tag is: photos carrying it or a descendant (approved or
    # not), out of all photos some model has seen.
    tagged, indexed = (await db.execute(text(f"""
        SELECT (SELECT count(DISTINCT pt.photo_id) FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                WHERE t.path <@ CAST(:path AS ltree)),
               (SELECT count(*) FROM photos p WHERE p.status = 1 AND {_HAS_VECTOR})
    """), p)).one()
    try:
        ens = await asyncio.get_running_loop().run_in_executor(
            None, fit_ensemble, xs, masks, y, is_weak, tagged / max(indexed, 1))
    except ValueError:
        return None
    by_key = {sp.key: sp for sp in spaces}
    used = [by_key[e.space] for e in ens.experts]
    used_images = [sp for sp in used if not isinstance(sp, pc.ContextSpace)]
    if not used_images:
        return None   # context alone cannot find or judge photos
    n_pos, n_neg = await label_counts(db, path)

    # Unverified links: how much the ensemble believes each one.
    linked = await _ids(db, """
        SELECT photo_id FROM photo_tags WHERE tag_id = :tid AND verified_at IS NULL""", p)
    if linked:
        lx, lm = await _matrix(db, used, linked, ctx)
        seen = np.logical_or.reduce([lm[s.key] for s in used_images])
        scores = ens.scores(lx, lm, library=False)
        keep = [(pid, float(s)) for pid, s, ok in zip(linked, scores, seen) if ok]
        if keep:
            await db.execute(text("""
                UPDATE photo_tags pt SET model_score = s.score
                FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS real[])) AS s(pid, score)
                WHERE pt.photo_id = s.pid AND pt.tag_id = :tid
            """), {"ids": [k for k, _ in keep], "scores": [s for _, s in keep], "tid": tag_id})

    # New suggestions: each model's best candidates, pooled, then scored by
    # the whole ensemble. Photos in the weak sample use their held-out score.
    await _hnsw_settings(db, MAX_SUGGESTIONS)
    where = f"{_SUGGESTABLE} AND p.id <> ALL(CAST(:sampled AS bigint[]))"
    pool: set[int] = set()
    for e, sp in zip(ens.experts, used):
        if e.weight <= 0 or isinstance(sp, pc.ContextSpace):
            continue   # context experts re-score; they cannot search
        rows = (await db.execute(text(sp.nearest_sql(where)), {
            **p, "w": ei.pgvec(e.coef[:-1]), "k": MAX_SUGGESTIONS, "sampled": weak_ids})).all()
        pool.update(r[0] for r in rows)
    picks: list[tuple[int, float]] = []
    if pool:
        cand = sorted(pool)
        cx, cm = await _matrix(db, used, cand, ctx)
        picks = [(pid, float(s)) for pid, s in zip(cand, ens.scores(cx, cm)) if s >= THRESHOLD]
    if ens.weak_scores is not None:
        picks += [(pid, float(s)) for pid, s in zip(weak_ids, ens.weak_scores) if s >= THRESHOLD]
    picks = sorted(picks, key=lambda t: -t[1])[:MAX_SUGGESTIONS]
    await db.execute(text("DELETE FROM tag_suggestions WHERE tag_id = :tid"), {"tid": tag_id})
    if picks:
        await db.execute(text("""
            INSERT INTO tag_suggestions (photo_id, tag_id, score, source)
            SELECT s.pid, :tid, s.score, 'model'
            FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS real[])) AS s(pid, score)
            ON CONFLICT (photo_id, tag_id)
                DO UPDATE SET score = EXCLUDED.score, source = 'model', created_at = now()
        """), {"tid": tag_id, "ids": [i for i, _ in picks], "scores": [s for _, s in picks]})

    experts = [{"space": e.space, "label": sp.label, "weight": round(e.weight, 4),
                "cv_recall": e.cv_recall, "cv_agreement": e.cv_agreement, "photos": e.photos}
               for e, sp in zip(ens.experts, used)]
    await db.execute(text("""
        INSERT INTO tag_models (tag_id, trained_at, embedding, coef, positives, negatives,
                                labels_at_train, cv_agreement, cv_recall, experts)
        VALUES (:tid, now(), :emb, :coef, :pos, :neg, :labels, :cva, :cvr, CAST(:experts AS jsonb))
        ON CONFLICT (tag_id) DO UPDATE SET
            trained_at = now(), embedding = EXCLUDED.embedding, coef = EXCLUDED.coef,
            positives = EXCLUDED.positives, negatives = EXCLUDED.negatives,
            labels_at_train = EXCLUDED.labels_at_train,
            cv_agreement = EXCLUDED.cv_agreement, cv_recall = EXCLUDED.cv_recall,
            experts = EXCLUDED.experts
    """), {"tid": tag_id, "emb": "+".join(e.space for e in ens.experts), "coef": ens.to_bytes(),
           "pos": len(pos_ids), "neg": len(neg_ids), "labels": n_pos + n_neg,
           "cva": ens.cv_agreement, "cvr": ens.cv_recall, "experts": json.dumps(experts)})
    await db.commit()
    return {"positives": len(pos_ids), "negatives": len(neg_ids), "weak_negatives": len(weak_ids),
            "cv_agreement": ens.cv_agreement, "cv_recall": ens.cv_recall,
            "experts": experts, "suggestions": len(picks), "scored_unverified": len(linked)}


async def retrain_if_due(db, tag_id: int) -> Optional[dict]:
    """Retrain after RETRAIN_EVERY new labels, or once a tag first has
    MIN_POSITIVES approved photos."""
    row = (await db.execute(text("""
        SELECT t.path::text AS path, m.labels_at_train
        FROM tags t LEFT JOIN tag_models m ON m.tag_id = t.id WHERE t.id = :t
    """), {"t": tag_id})).first()
    if not row:
        return None
    n_pos, n_neg = await label_counts(db, row.path)
    if row.labels_at_train is None:
        due = n_pos >= MIN_POSITIVES
    else:
        due = abs(n_pos + n_neg - row.labels_at_train) >= RETRAIN_EVERY
    return await train_tag(db, tag_id) if due else None


async def trainable_tags(db) -> list[int]:
    """Tags the user applies directly with at least MIN_POSITIVES approved
    photos (counting descendants), most-approved first."""
    rows = (await db.execute(text("""
        SELECT t.id, count(DISTINCT pt.photo_id) AS n
        FROM tags t
        JOIN tags d ON d.path <@ t.path
        JOIN photo_tags pt ON pt.tag_id = d.id AND pt.verified_at IS NOT NULL
        WHERE NOT t.is_person
          AND EXISTS (SELECT 1 FROM photo_tags own WHERE own.tag_id = t.id)
        GROUP BY t.id
        HAVING count(DISTINCT pt.photo_id) >= :min
        ORDER BY n DESC
    """), {"min": MIN_POSITIVES})).all()
    return [r[0] for r in rows]


# ── find by name ─────────────────────────────────────────────────────────────

def _prompts(path: str) -> list[str]:
    parts = path.split(".")
    leaf = parts[-1].replace("_", " ")
    out = [leaf]
    if len(parts) > 1:
        out.append(f"{leaf}, {parts[-2].replace('_', ' ').lower()}")
    return out


def _text_vector(space_key: str, path: str) -> Optional[np.ndarray]:
    """The tag's name through a model's text tower, or None if it has none."""
    from fernkam import clip_embed
    from fernkam import embed_models as em

    if space_key == "clip":
        if not clip_embed.models_downloaded():
            return None
        vecs = clip_embed.embed_text([f"a photo of a {p}." for p in _prompts(path)])
    else:
        if not em.text_installed(space_key):
            return None
        tmpl = em.text_prompt(space_key)
        vecs = em.embed_text(space_key, [tmpl.format(p) for p in _prompts(path)])
    v = np.asarray(vecs, np.float32).mean(axis=0)
    return v / max(float(np.linalg.norm(v)), 1e-12)


async def name_models(db) -> list[str]:
    """Spaces that can search by name right now: indexed and with a text tower."""
    from fernkam import clip_embed
    from fernkam import embed_models as em
    return [s.key for s in await ei.active_spaces(db)
            if (s.is_clip and clip_embed.models_downloaded()) or (not s.is_clip and em.text_installed(s.key))]


async def find_by_name(db, tag_id: int) -> dict:
    """Search the library for a tag by its name, before it has enough approved
    photos to learn from. Each model with a text tower ranks photos by how
    well they match "a photo of <name>"; the rankings are merged (reciprocal
    rank fusion) and the best NAME_SUGGESTIONS become suggestions marked as
    found by name. Does nothing for a tag that is already learning."""
    tag = (await db.execute(text(
        "SELECT t.path::text AS path, t.is_person, m.tag_id IS NOT NULL AS learning "
        "FROM tags t LEFT JOIN tag_models m ON m.tag_id = t.id WHERE t.id = :t"), {"t": tag_id})).first()
    if not tag or tag.is_person:
        return {"suggestions": 0, "models": []}
    if tag.learning:
        return {"suggestions": 0, "models": [], "learning": True}
    loop = asyncio.get_running_loop()
    p = {"path": tag.path, "tid": tag_id}
    await _hnsw_settings(db, NAME_SUGGESTIONS * 2)
    fused: dict[int, float] = {}
    used = []
    for key in await name_models(db):
        vec = await loop.run_in_executor(None, _text_vector, key, tag.path)
        if vec is None:
            continue
        sp = ei.space(key)
        rows = (await db.execute(text(sp.nearest_sql(_SUGGESTABLE)), {
            **p, "w": ei.pgvec(vec), "k": NAME_SUGGESTIONS * 2})).all()
        for rank, r in enumerate(rows):
            fused[r[0]] = fused.get(r[0], 0.0) + 1.0 / (60 + rank)
        used.append(key)
    if not fused:
        return {"suggestions": 0, "models": used}
    # A linked species is not suggested where and when GBIF has no record of it.
    table = await sr.RangeTable.load(db, tag_id)
    if table is not None:
        meta = await pc.photo_meta(db, list(fused))
        for pid in list(fused):
            lat, lon, _d, _h, month = meta.get(pid, (None,) * 5)
            st = table.status(lat, lon, month)
            if st and st["status"] == "absent":
                del fused[pid]
    top = sorted(fused.items(), key=lambda t: -t[1])[:NAME_SUGGESTIONS]
    best = len(used) / 60.0    # a photo ranked first by every model
    await db.execute(text("DELETE FROM tag_suggestions WHERE tag_id = :tid AND source = 'name'"), p)
    await db.execute(text("""
        INSERT INTO tag_suggestions (photo_id, tag_id, score, source)
        SELECT s.pid, :tid, s.score, 'name'
        FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS real[])) AS s(pid, score)
        ON CONFLICT (photo_id, tag_id) DO NOTHING
    """), {"tid": tag_id, "ids": [i for i, _ in top], "scores": [min(1.0, s / best) for _, s in top]})
    await db.commit()
    return {"suggestions": len(top), "models": used}
