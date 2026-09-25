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

Each tag gets one logistic regression over photos.embedding_v (CLIP, unit
length). With x of unit length, ranking photos by w·x + b is ranking by
cosine to w, so the HNSW index finds a tag's best candidates directly.

A random photo used as a weak negative might really be the tag, just
untagged. The final model has learned to score it down, so such photos are
judged by their cross-validation score instead, from models that never saw
them (PU bagging). Otherwise the photos most worth suggesting could be the
ones the model can no longer find.

Scores are calibrated as if the tag were on half of all photos. That fits
an unverified tag (being tagged is already evidence), but not a search of
the whole library, where most photos are not the tag. For suggestions the
tag's prevalence is added back in (Bayes' rule for a prior shift), so a
suggestion means "more likely than not", not "looks more like the tag than
like a random photo".

A model's suggestions go into tag_suggestions for review. Accepting or
rejecting them adds labels, and after RETRAIN_EVERY new labels the tag is
retrained. That is the feedback loop.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy import text

logger = logging.getLogger(__name__)

EMBEDDING = "clip-vit-b32"   # which photos.embedding_v a stored model was trained on
MIN_POSITIVES = 8            # approved photos before a tag starts learning
RETRAIN_EVERY = 10           # new approvals/rejections before a tag retrains
MAX_POSITIVES = 3000         # sampled above this; keeps training under a few seconds
WEAK_NEG_PER_POSITIVE = 10
WEAK_NEG_MIN, WEAK_NEG_MAX = 500, 4000
WEAK_WEIGHT = 0.25           # a random photo is only probably not this tag
THRESHOLD = 0.5
MAX_SUGGESTIONS = 300        # per tag, best first
MIN_PREVALENCE = 0.002       # floor for the prior of a newly used tag


@dataclass
class Fit:
    coef: np.ndarray          # (dim + 1,) float32: weights, then bias, on raw embeddings
    shift: float              # log-odds of the tag's prevalence, for library-wide scores
    cv_agreement: Optional[float]
    cv_recall: Optional[float]
    weak_scores: Optional[np.ndarray] = None   # held-out library-wide score of each weak negative

    def scores(self, x: np.ndarray, library: bool = True) -> np.ndarray:
        """library=True: chance a photo from the library has the tag.
        library=False: how much a photo already tagged with it looks right."""
        return _sigmoid(x @ self.coef[:-1] + self.coef[-1] + (self.shift if library else 0.0))


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


def fit(pos: np.ndarray, neg: np.ndarray, weak: np.ndarray, prevalence: float) -> Fit:
    """Train on approved positives, rejected negatives and weak negatives, and
    measure it with 5-fold cross-validation on the same examples. prevalence
    is the share of the library that has the tag, as far as is known.

    cv_recall: share of approved photos the model would suggest (library-wide
    score, prevalence included).
    cv_agreement: share of the user's own decisions (approved and rejected)
    the model gets right, judged like an unverified tag (without prevalence).
    Weak negatives are left out of both: some of them are untagged positives,
    so they cannot say whether the model is wrong. Precision on new photos is
    measured by the review itself (hit rate).
    """
    x = np.vstack([pos, neg, weak]).astype(np.float32)
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg) + len(weak))]).astype(int)
    is_weak = np.concatenate([np.zeros(len(pos) + len(neg), bool), np.ones(len(weak), bool)])

    prevalence = min(max(prevalence, MIN_PREVALENCE), 0.5)
    shift = float(np.log(prevalence / (1 - prevalence)))
    cv_a = cv_r = weak_scores = None
    a, b = 1.0, 0.0
    folds = min(5, len(pos))
    if folds >= 2:
        from sklearn.model_selection import StratifiedKFold

        oof = np.zeros(len(y))
        for tr, te in StratifiedKFold(folds, shuffle=True, random_state=0).split(x, y):
            c = _train(x[tr], y[tr], is_weak[tr])
            oof[te] = x[te] @ c[:-1] + c[-1]
        a, b = _calibrate(oof, y, _weights(y, is_weak))
        library = _sigmoid(a * oof + b + shift)
        weak_scores = library[is_weak]
        cv_r = float((library[y == 1] >= THRESHOLD).mean())
        judged = ~is_weak
        says_yes = _sigmoid(a * oof[judged] + b) >= THRESHOLD
        cv_a = float((says_yes == (y[judged] == 1)).mean())
    c = _train(x, y, is_weak)
    return Fit(np.append(c[:-1] * a, c[-1] * a + b).astype(np.float32), shift, cv_a, cv_r, weak_scores)


# ── database side ────────────────────────────────────────────────────────────

def _vec(s: str) -> np.ndarray:
    return np.array(s[1:-1].split(","), dtype=np.float32)


def _pgvec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def _vectors(db, sql: str, params: dict) -> tuple[list[int], np.ndarray]:
    """Rows of (photo id, embedding text) -> ids and an (n, dim) matrix."""
    rows = (await db.execute(text(sql), params)).all()
    if not rows:
        return [], np.zeros((0, 0), np.float32)
    return [r[0] for r in rows], np.vstack([_vec(r[1]) for r in rows])


# A tag's positives: approved links to it or a descendant, with an embedding.
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


async def train_tag(db, tag_id: int) -> Optional[dict]:
    """Train one tag, score its unverified links and refresh its suggestions.
    Returns the model's stats, or None when the tag has too few approved photos
    with embeddings. Commits."""
    tag = (await db.execute(text(
        "SELECT path::text, is_person FROM tags WHERE id = :t"), {"t": tag_id})).first()
    if not tag or tag.is_person:
        return None
    path = tag.path
    p = {"path": path, "tid": tag_id}

    _, pos = await _vectors(db, f"""
        SELECT id, embedding_v::text FROM photos
        WHERE id IN ({_POSITIVE_PHOTOS}) AND embedding_v IS NOT NULL AND status = 1
        ORDER BY random() LIMIT {MAX_POSITIVES}
    """, p)
    if len(pos) < MIN_POSITIVES:
        return None
    _, neg = await _vectors(db, f"""
        SELECT id, embedding_v::text FROM photos
        WHERE id IN ({_NEGATIVE_PHOTOS}) AND embedding_v IS NOT NULL
    """, p)
    n_weak = min(max(len(pos) * WEAK_NEG_PER_POSITIVE, WEAK_NEG_MIN), WEAK_NEG_MAX)
    # Weak negatives: any photo not tagged with this tag or a descendant, in
    # any state. An unverified link might be right, so it is left out.
    weak_ids, weak = await _vectors(db, f"""
        SELECT p.id, p.embedding_v::text FROM photos p
        WHERE p.status = 1 AND p.embedding_v IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                          WHERE pt.photo_id = p.id AND t.path <@ CAST(:path AS ltree))
          AND NOT EXISTS (SELECT 1 FROM tag_rejections r
                          WHERE r.photo_id = p.id AND r.tag_id = :tid)
        ORDER BY random() LIMIT {n_weak}
    """, p)
    dim = pos.shape[1]
    neg = neg if len(neg) else np.zeros((0, dim), np.float32)
    weak = weak if len(weak) else np.zeros((0, dim), np.float32)
    if len(neg) + len(weak) == 0:
        return None

    # How common the tag is: photos carrying it or a descendant (approved or
    # not), out of all indexed photos.
    tagged, indexed = (await db.execute(text("""
        SELECT (SELECT count(DISTINCT pt.photo_id) FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                WHERE t.path <@ CAST(:path AS ltree)),
               (SELECT count(*) FROM photos WHERE status = 1 AND embedding_v IS NOT NULL)
    """), p)).one()
    result = await asyncio.get_running_loop().run_in_executor(
        None, fit, pos, neg, weak, tagged / max(indexed, 1))
    w = _pgvec(result.coef[:-1])
    b = float(result.coef[-1])
    n_pos, n_neg = await label_counts(db, path)

    # Unverified links: how much the model believes each one.
    linked = (await db.execute(text("""
        SELECT pt.photo_id, -(p.embedding_v <#> CAST(:w AS vector))
        FROM photo_tags pt JOIN photos p ON p.id = pt.photo_id
        WHERE pt.tag_id = :tid AND pt.verified_at IS NULL AND p.embedding_v IS NOT NULL
    """), {"w": w, "tid": tag_id})).all()
    if linked:
        ids = [r[0] for r in linked]
        scores = _sigmoid(np.array([r[1] for r in linked]) + b).tolist()
        await db.execute(text("""
            UPDATE photo_tags pt SET model_score = s.score
            FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS real[])) AS s(pid, score)
            WHERE pt.photo_id = s.pid AND pt.tag_id = :tid
        """), {"ids": ids, "scores": scores, "tid": tag_id})

    # New suggestions: the best photos not tagged with it (or a descendant)
    # and not rejected for it. A photo with only an ancestor tag can still be
    # suggested the more specific one. Photos in the weak sample are judged by
    # their held-out score instead (see the module docstring).
    if await _supports_iterative_scan(db):
        await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await db.execute(text(f"SET LOCAL hnsw.ef_search = {MAX_SUGGESTIONS * 2}"))
    near = (await db.execute(text("""
        SELECT p.id, -(p.embedding_v <#> CAST(:w AS vector)) AS ip
        FROM photos p
        WHERE p.status = 1 AND p.embedding_v IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                          WHERE pt.photo_id = p.id AND t.path <@ CAST(:path AS ltree))
          AND NOT EXISTS (SELECT 1 FROM tag_rejections r
                          WHERE r.photo_id = p.id AND r.tag_id = :tid)
          AND p.id <> ALL(CAST(:sampled AS bigint[]))
        ORDER BY p.embedding_v <=> CAST(:w AS vector)
        LIMIT :k
    """), {"w": w, "path": path, "tid": tag_id, "k": MAX_SUGGESTIONS, "sampled": weak_ids})).all()
    picks = [(r[0], float(s)) for r, s in zip(near, _sigmoid(np.array([r[1] for r in near]) + b + result.shift))
             if s >= THRESHOLD] if near else []
    if result.weak_scores is not None:
        picks += [(pid, float(s)) for pid, s in zip(weak_ids, result.weak_scores) if s >= THRESHOLD]
    picks = sorted(picks, key=lambda x: -x[1])[:MAX_SUGGESTIONS]
    await db.execute(text("DELETE FROM tag_suggestions WHERE tag_id = :tid"), {"tid": tag_id})
    if picks:
        await db.execute(text("""
            INSERT INTO tag_suggestions (photo_id, tag_id, score)
            SELECT s.pid, :tid, s.score
            FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS real[])) AS s(pid, score)
            ON CONFLICT (photo_id, tag_id)
                DO UPDATE SET score = EXCLUDED.score, created_at = now()
        """), {"tid": tag_id, "ids": [i for i, _ in picks], "scores": [s for _, s in picks]})

    await db.execute(text("""
        INSERT INTO tag_models (tag_id, trained_at, embedding, coef, positives, negatives,
                                labels_at_train, cv_agreement, cv_recall)
        VALUES (:tid, now(), :emb, :coef, :pos, :neg, :labels, :cva, :cvr)
        ON CONFLICT (tag_id) DO UPDATE SET
            trained_at = now(), embedding = EXCLUDED.embedding, coef = EXCLUDED.coef,
            positives = EXCLUDED.positives, negatives = EXCLUDED.negatives,
            labels_at_train = EXCLUDED.labels_at_train,
            cv_agreement = EXCLUDED.cv_agreement, cv_recall = EXCLUDED.cv_recall
    """), {"tid": tag_id, "emb": EMBEDDING, "coef": result.coef.tobytes(),
           "pos": len(pos), "neg": len(neg), "labels": n_pos + n_neg,
           "cva": result.cv_agreement, "cvr": result.cv_recall})
    await db.commit()
    return {"positives": len(pos), "negatives": len(neg), "weak_negatives": len(weak),
            "cv_agreement": result.cv_agreement, "cv_recall": result.cv_recall,
            "suggestions": len(picks), "scored_unverified": len(linked)}


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
