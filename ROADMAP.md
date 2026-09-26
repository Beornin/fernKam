# fernKam Roadmap

**Thesis:** digiKam is a file browser that remembers things. fernKam is a search
engine that happens to hold files.

digiKam cannot follow us here, and not for lack of effort — it is
architecturally unable to use this hardware:

- `grep -rl "faiss|hnsw|ivfflat|ANN" core/` over the full digiKam source →
  **zero hits.** No approximate nearest-neighbour anywhere. Face matching is a
  linear scan that degrades as the library grows.
- CUDA appears in exactly one path (OpenCL via OpenCV DNN, face extraction).
  Thumbnails, RAW decode, and scanning are all CPU-bound.

fernKam already runs Postgres + pgvector with working HNSW indexes over 79k face
vectors, on a 24 GB GPU. That is the whole advantage. Everything below compounds
on it.

---

## Where it is at today

Measured against the live catalogue, not estimated.

| | | |
|---|---|---|
| photos catalogued | 116,232 | |
| photos with **zero tags** | **60,755 (52%)** | invisible except by folder/date |
| photos with title or caption | 3,825 (3%) | full-text search covers 3% of the library |
| photos rated > 0 | **73 (0.06%)** | the cull loop effectively does not exist |
| folders hand-maintained | 3,870 | |
| faces detected | 79,263 across 42,576 photos | |
| face embeddings + HNSW | 79,261 | built |
| people known | 78 | |
| faces **awaiting review** | **23,628** | 52% have a same-person peer — untagged people, see Phase 1 |
| photos with a face but nobody named | **5,883** | browsable via the `unnamed_faces` filter (Phase 1) |
| photo-level embeddings | **CLIP 512-d + HNSW** | powers semantic search, tag propagation, near-dupes (Phase 2) |
| database size | **34 GB -> 1,775 MB** | thumbnails moved to disk (Phase 0.2) |
| no-op rescan | **7,632 s -> 11.8 s** | 647x, skips unchanged files (Phase 0.1) |

Legend: DONE / PARTIAL / TODO

---

## Phase 0 — Delete · DONE

No new features. Pure removal, largest speed win in the plan.

| # | Change | Effect | Status |
|---|---|---|---|
| 0.1 | Skip unchanged files during scan | **7,632 s → 11.8 s (647x)** | DONE |
| 0.2 | Move `photo_thumbnails` out of Postgres to disk | **34 GB DB → 2.2 GB** | DONE |
| 0.3 | Drop `people` table (0 rows) and `audit_log` (0 rows) | dead schema | DONE |
| 0.4 | Drop `ix_photos_exif_gin` (25 MB, 0 scans) + 4 other unused indexes | write overhead | DONE |

**0.1 root cause.** In `importers/filesystem.py`, every known file was appended
to `existing_to_update` unconditionally. The stat'd `mtime` was captured and then
never compared against the stored `file_modified_at_sync`, so exiftool re-read
all 122k files on every scan. That column was already populated on
**116,232 / 116,232** rows, so no backfill was needed.

Measured after the change, dry-running the real walk over the live library:

```
preload 116,232 rows: 0.3s
walk 6,601 dirs:     11.5s
  NEW (import)              1
  CHANGED (re-read)         0
  UNCHANGED (SKIPPED) 116,184   <- previously all re-read via exiftool
```

Forcing a full metadata re-read is still available separately via
`/sync/metadata` (`refresh_metadata_from_files`), so nothing was lost.
Covered by `backend/tests/test_scan_skip.py`.

**0.2 — where thumbnails go, and why it stays snappy.**

Destination is already defined: `thumb_cache_path()` computes
`data/thumbnails/{id % 1000}/{id}_{size}.webp`, and `THUMB_CACHE_DIR` is already
configured. 431,495 files across 1000 buckets is ~432 per directory. Nothing new
to design — `get_or_create_thumbnail()` writes files today, the path just is not
wired into the media router, which reads from the DB instead.

Measured, 300 reads per size on this machine:

| size | Postgres | disk | avg bytes |
|---|---|---|---|
| sm | 0.48 ms | **0.082 ms** | 8 KB |
| md | 0.66 ms | **0.075 ms** | 26 KB |
| lg | 0.69 ms | **0.081 ms** | 80 KB |
| xl | 0.82 ms | **0.090 ms** | 152 KB |

Disk is 7–9x faster and flat across size; Postgres scales with TOAST chunk count
(an xl thumbnail is 152,236 bytes = **76 chunks per read**). But both are fast in
absolute terms, so **latency is not the reason to move.** The reasons are:

1. **Connection pool — this is the one that is actually felt.** Pool is 20 + 30
   overflow. All four sizes are in real use (xl to inspect detail without opening,
   sm to scan for something specific), so changing size re-requests every visible
   tile at a new URL. Simulating one slider move to xl — 42 tiles at once, with the
   grid's own catalogue query racing them:

   | | catalogue query | burst total |
   |---|---|---|
   | idle pool | 0.67 ms | — |
   | **42 xl in flight via Postgres** | **19.19 ms** | 19 ms |
   | 42 xl in flight from disk | 2.55 ms | 5 ms |

   **29x slower.** The query populating the grid queues behind 42 thumbnail reads
   for the same 50 connections. On disk, reads go through the executor threadpool
   and never take a connection.
2. **Backups.** `backup.py` runs plain `pg_dump` with no table exclusion, so
   every backup dumps all 31 GB of TOAST. Thumbnails are regenerable cache.
3. **`shared_buffers` is 2 GB** and 31 GB of thumbnail TOAST churns through it,
   evicting real catalogue pages.

Browsing is already cached client-side (`Cache-Control: public, max-age=86400`),
so re-scrolling never hits the server for 24 h. The difference only shows on cold
first paint.

**Cost of the move, as built:** the FK was `ON DELETE CASCADE`, so photo deletion
used to clean up thumbnails for free. In practice this mattered less than
expected — `dedup.py` and `photos.py` both *soft*-delete (`status = 0`), so the
cascade never fired for them. Only the scanner's removed-file cleanup hard-deletes
rows, so that is the single site calling `delete_thumbnails_from_disk()`.
`reset-db` also clears the cache directory, which its `TRUNCATE` would otherwise
orphan.

Writes go to a unique temp name and `os.replace()` into place, so a concurrent
reader never sees a half-written file.

**Deleted with this item:** `get_thumbnail_from_db`, `store_thumbnail_to_db`, and
`get_or_create_thumbnail` (48 lines, zero callers). `thumbnails.py` is net −71
lines despite gaining the three disk functions.

**Result, verified.** 431,495 thumbnails copied out in 557 s (28.3 GB), then
checked before dropping anything: all 431,495 rows had a matching file, and a
500-row random sample was byte-identical. Migration `0024` then dropped the
table.

```
database size:  34 GB  ->  1,775 MB
largest table:  photo_thumbnails 32 GB  ->  faces 1,488 MB
```

Served over HTTP, all four cached sizes return in 2.6-4.3 ms and decode as valid
WebP at the right dimensions (240x162 / 480x324 / 960x648 / 1440x973). The miss
path was exercised with an `xxl` that had never been generated: 1.36 s to build
and it correctly wrote `<id>_xxl.webp` into the cache.

**Two one-line wins that do not require the move:**
`ALTER TABLE photo_thumbnails ALTER COLUMN data SET STORAGE EXTERNAL` (storage is
currently `x`/EXTENDED, so Postgres attempts pglz compression on every write of
already-compressed WebP and gains nothing; new rows only), and
`pg_dump --exclude-table=photo_thumbnails` to strip 31 GB from every backup.
Both became moot once the table was dropped outright (migration `0024`).

**0.3 / 0.4 — what was removed** (migration `0023`).

`people` held 0 rows and `faces.person_id` 0 non-null values; the people feature
runs entirely on `tags.is_person` + `faces.person_tag_id`, so the table and its
FK column were never written by any code path. `audit_log` held 0 rows. Both ORM
models, the `Face.person` relationship, and the `people`/`audit_log` entries in
`reset-db` went with them.

Indexes dropped, all reporting 0 scans in `pg_stat_user_indexes`:
`ix_photos_exif_gin` (25 MB), `ix_photos_imported_at` (8.4 MB), `ix_photos_city`,
`ix_photos_country_code`, `ix_photos_duration_secs`.

Verified after migrating: `people`/`audit_log`/`faces.person_id` gone, all five
indexes gone, and `ix_photos_search_tsv`, `ix_faces_embedding_v_hnsw`,
`ix_photos_sha256`, `ix_photo_tags_tag_id` still present with 79,263 faces and
116,232 photos readable.

**One bug introduced and caught here:** 0.1 changed `existing_photos` values from
a bare id to an `(id, mtime)` tuple, which silently broke the scanner's
removed-file cleanup where it unpacked the value as `photo_id`. Fixed, and the
per-row deletes were batched into one `IN (...)` while open.

**One pre-existing bug found here.** Verifying 0.2 needed the server running, and
it died on startup with no usable error. Cause: `api/app.py` prints U+2713/U+2717
marks in its startup banner, which a cp1252 Windows console cannot encode — and
two of those prints sit in `except` blocks, so a real failure raised
`UnicodeEncodeError` *while reporting itself* and killed the worker with the
actual cause hidden. Fixed once at the stream rather than six times at the call
sites: `sys.stdout`/`sys.stderr` are reconfigured to UTF-8 with
`errors="replace"` at import, so they can never raise on encoding again.
Verified by starting under a forced `PYTHONIOENCODING=cp1252`.

---

## Catalogue integrity — duplicate photo rows (migration 0026)

A photo that would not open no matter how often the library was rescanned. The
row pointed at `AA_RAW/…/jpg/_DSC2575.jpg`, the file had since been moved to the
portfolio, and rescanning never cleared it.

**Cause.** `scan_library` builds `existing_photos` as a dict keyed by
`(album_path, filename)`. A dict collapses duplicates, so a second row for the
same file is invisible to every phase of the scan — never matched against disk,
never refreshed, never deleted. It accumulates permanently.

Two scans running at once is all it takes; each sees the file as new and
inserts it. The scan history showed exactly that: runs two minutes apart
importing +2,952 and +2,852.

```
before   121,253 rows   2,833 duplicated (album_path, filename) groups
after    118,420 rows           0
AA_RAW/20260919_00028/jpg:  824 rows -> 412, against 412 files on disk
```

Cost of the cleanup, measured: 1 face record (re-detectable), 0 tags, 0
embeddings, 0 ratings — the duplicate copies were empty because the real work
had attached to the first row.

**Fix.** A unique index on `(album_path, filename)`. Fixing the importer would
mean trusting every future caller; the index cannot be bypassed. Verified that a
duplicate insert is now rejected, and covered by
`backend/tests/test_no_duplicate_photos.py`.

**Concurrency guard (follow-up).** The unique index makes the *corruption*
impossible, but two scans at once is still wasted work and a scan racing a
file-move can delete rows for files that merely moved. Rather than a queue —
persistence, ordering, a worker loop, starvation, all for one person who
occasionally clicks twice — only the four tasks that touch the filesystem now
exclude each other:

```
scan_library · workflow_sorting · workflow_remove_nonkeep_raw · workflow_move_raws
```

Everything else keeps running alongside. Serialising the whole app would mean
not being able to search during a 20-minute embed, which is a worse app for no
safety gain — embedding, search, face work and geocoding only *read* files.

The check lives inside `create_task`, not at each call site, so an endpoint
written later cannot forget it; `TaskConflict` maps to HTTP 409 via one handler
in `api/app.py`. `cancel_stale_tasks()` already clears "running" rows at
startup, so a crash cannot leave the guard stuck on.

Verified live: a second scan returns 409 naming the running task, a *different*
file workflow also returns 409, and semantic search, embed status and the photo
grid all return 200 throughout. Covered by
`backend/tests/test_task_conflict.py`.

**Also fixed:** the scan's completion message reported only imports, refreshes
and faces, so a scan that removed 400 stale rows read identically to one that
did nothing — which is why rescanning never appeared to help. It now reports
removals and unchanged counts.

---

## Phase 1 — Find and tag the people who are not tagged yet · DONE

> **Rewritten twice, both times because a measurement was wrong.** The first
> draft proposed bulk-archiving 96% of the review queue, based on similarity to
> known-person *centroids* — which answers "is this someone already tagged?",
> not "is this a person who appears repeatedly?" Measured face-to-face, 52% of
> the queue has a same-person peer at >= 0.60, so that would have discarded
> ~11,800 real faces. Dropped. The second draft recommended clustering at
> threshold 0.65 on the strength of a "largest cluster 221" measurement that
> turned out to be an artifact — see **The measurement trap** below.

**Nothing here removed anything that worked.** Face scanning, rescanning,
manual tagging, and the confirm-then-rematch loop are all intact.

### What already worked (verified, not rebuilt)

Confirming a face spawns `_auto_confirm_similar(person_tag_id, [face_id])`,
which searches every unreviewed face against the newly confirmed one **used
directly as a seed**, with a TWINS carve-out and a one-person-per-photo guard.
The "new face feeds back into finding others" behaviour was already wired.

Labelling a whole cluster also already existed in the review UI
(`assignClusterTo()`, with per-face deselection plus ignore/delete). Item 1.4
needed no code.

### The measurement trap

`_rebuild_face_clusters` uses `LIMIT :k` as a **bind parameter**. Postgres
cannot plan for an unknown limit, so it performs an **exact** nearest-neighbour
scan rather than using the HNSW index. Reproducing the query offline with a
*literal* `LIMIT 10` silently switches to approximate search, under-retrieves
neighbours, and reports far smaller components than production actually builds:
**6,390 edges vs 14,263** at the same threshold and gates.

Every "largest cluster" figure in the first analysis came from that approximate
path and was optimistic by roughly 5x. Re-measured with exact search:

| thresh | clusters | faces in | largest | <= 20 faces |
|---|---|---|---|---|
| 0.65 | 1,138 | 4,374 | **1,236** | 1,136 |
| 0.72 | 858 | 2,665 | 645 | 856 |
| 0.78 | 602 | 1,656 | 287 | 601 |
| **0.82** | **452** | **1,131** | **112** | **451** |
| 0.86 | 315 | 716 | 12 | 315 |

At every setting only one or two components are oversized and the rest are
already small, so the threshold really chooses how much chaining to tolerate in
the single worst cluster. 0.82 is the default: 0.86 is cleaner but gives up 40%
of the clusters for it.

### Why one cluster held 1,440 faces

Sharpness, not the threshold. That component spanned **1,434 distinct photos
from 2006 to 2026 across every album**:

| | blob | other clusters |
|---|---|---|
| avg width | 564 px | 344 px |
| avg det_score | 0.75 | 0.81 |
| **avg blur_score** | **94.9** | **169.9** |

Large faces, half as sharp. An out-of-focus face's embedding collapses toward a
generic mean, so every blurry face resembles every other one regardless of
identity. `blur_score` was already computed on every face and never used as a
clustering gate.

### Items

| # | Change | Result | Status |
|---|---|---|---|
| 1.1 | `blur_score` + `det_score` gates in `_rebuild_face_clusters` | blur gate added; `min_det` had been measured with but never wired | DONE |
| 1.2 | Cluster `suggested` faces, not just `unconfirmed` | 255 previously-ineligible faces now cluster | DONE |
| 1.3 | Rebuild clusters (stale since 2026-07-29) | 452 clusters, largest cohesion **0.629** (was 0.343) | DONE |
| 1.4 | Label a whole cluster at once | already existed — no code written | DONE |
| 1.5 | Refresh the person centroid on manual confirm | both confirm paths unified | DONE |
| 1.6 | Find photos with a face but nobody named | `unnamed_faces` filter — **5,883 photos**, exact match to ground truth | DONE |

**1.5 found a second bug.** There were *two* manual-confirm paths, and the batch
one skipped its background job entirely for more than 5 faces — so **labelling a
cluster never refreshed that person's centroid**. Both now route through one
`_relearn_person_bg`, which always refreshes the centroid and skips only the
seed propagation for large batches (that part would hammer the DB).

**1.6 reuses the photo grid** rather than adding a page: one `unnamed_faces`
filter in `PhotoFilters` plus one option in the existing Faces dropdown, so
those photos are browsable with every tool that already exists.

### Result

```
before (7 weeks stale):  3,883 faces  1,267 clusters  largest 1,041 (cohesion junk)
after:                   1,131 faces    452 clusters  largest   112 (cohesion 0.629)
                         sizes: [112, 8, 7, 6, 6, 6, 5, 5, 5, 5]
```

Fewer faces clustered, but the old count was mostly one garbage component. Every
cluster now holds faces that genuinely belong together — the largest at 0.629
and second at 0.829 internal similarity are both plausibly single people.

Faces that never cluster stay reachable through manual tagging and the existing
per-face suggestion flow. Clustering is an accelerator, not the only path.

The pre-rebuild mapping is preserved in `face_clusters_backup_pre_phase1`; drop
it once the new clusters have proven themselves in review.

---

## Phase 2 — Smart auto-tagging · DONE

The flagship, and the part digiKam structurally cannot follow (zero ANN code in
its entire source). All four features ride on **one** column — `photos.embedding_v`,
a 512-d CLIP vector — and one HNSW index.

### Model choice: no new ML stack

CLIP ViT-B/32 runs through **onnxruntime, already a dependency** (the face
pipeline uses it), so nothing heavyweight was added. Both ONNX towers emit the
*projected* 512-d vectors, so image and text embeddings share one space and
compare directly with pgvector cosine distance — no separate projection step.

Only one new dependency: `tokenizers` (small Rust wheel) for CLIP's BPE.
Hand-rolling that is ~150 lines of bug farm.

The 606 MB of model files download on first use into `data/models/clip`,
mirroring how InsightFace caches `buffalo_l`. They are deliberately **not**
bundled into the PyInstaller build, which would add 606 MB to the exe.

Measured on the 3090: **~215 img/s** raw inference, ~92/s end-to-end including
image load. Embeddings are computed from the cached 480px thumbnails rather than
the originals — CLIP crops to 224px anyway, so decoding a 100 MB RAW to feed it
would be pure waste. Phase 0.2 putting thumbnails on disk is what makes this cheap.

### Items

| # | Change | Result | Status |
|---|---|---|---|
| 2.1 | CLIP embeddings → `photos.embedding_v` + HNSW | migration `0025`; ~92 photos/s | DONE |
| 2.2 | **Tag propagation by kNN** | suggests from your own taxonomy | DONE |
| 2.3 | Text → image search | works on untagged photos | DONE |
| 2.4 | Visual near-duplicate detection | catches what sha256 misses | DONE |
| 2.5 | Accept/reject suggested tags in bulk | `/discover` page | DONE |

### Verified on the live library

Semantic search, with only ~10% of the library indexed at the time:

```
"a dog playing outside"      -> 0.316  Family Adventures/Dog Park 3-24-2021
"birthday cake with candles" -> 0.317  Birthdays/Ben 33rd
"sunset over water"          -> 0.302  Anniversaries (evening shots)
```

Tag propagation on a photo with **zero** tags, from its nearest tagged
neighbours — note it proposes the existing hierarchical tags, not generic labels:

```
w=10.91  votes=15  People.<person A>
w= 6.54  votes= 9  People.<person B>
w= 5.11  votes= 7  People.<person C>
```

Visual near-duplicates on the same photo: 0.985 / 0.984 / 0.979 against other
frames from that session — the re-export/crop/resize case that sha256-exact
dedup cannot see.

`apply-tags` was round-tripped against the live DB and is idempotent
(`ON CONFLICT DO NOTHING`); the test write was reverted.

### Design note: suggestions stay suggestions

`/semantic/suggest-tags` never writes. The library holds 1,415 hand-curated tags
across 55k photos and that curation is the asset worth protecting, so applying
tags is a separate explicit call. The `/discover` panel applies accepted tags to
the whole current selection, so one confirmed suggestion can still cover a batch.

`/discover` reuses `PhotoGrid` and `PhotoLightbox` rather than reimplementing a
results view.

---

## Phase 3 — Portfolio and RAW/SORTING as the front door · DONE

The workflows digiKam could not handle. They existed as 390 lines of solid
scripts behind a generic page; this makes them visible, safe, and connected to
Phase 2.

### Items

| # | Change | Result | Status |
|---|---|---|---|
| 3.1 | Pipeline stage view with live counts | intake -> staging -> archive -> portfolio | DONE |
| 3.2 | Dry-run preview on every workflow | all four, defaulting to preview | DONE |
| 3.3 | Auto-suggest subject tags for a folder via Phase 2 kNN | `/semantic/suggest-tags-bulk` | DONE |
| 3.4 | RAW/JPEG pair health check | **1,063 orphan RAWs found** | DONE |
| 3.5 | Promote to portfolio: rate + tag + move + keep stacks intact | `/workflows/promote-to-portfolio` | DONE |

### 3.2 — dry run is the default, not a flag you remember

All four workflows took `run(...)` and did the work immediately. Two of them
delete or relocate originals. Every one now takes `dry_run: bool = True`, so the
destructive path has to be asked for. The UI splits this into **Preview** and
**Apply**, and Apply confirms first.

**A bug this surfaced:** the Preview button was wired for all four cards before
`sync_stack_tags` had a `dry_run` parameter. Pydantic silently ignores unknown
fields by default, so "Preview" on that card would have written XMP to every
stack member for real. Fixed by giving it a real dry-run *and* setting
`extra="forbid"` on all four request models — an unknown field is now a 422
instead of a silent full-speed run.

Verified against the live library:

```
Found 485 RAW files. 45 have no matching picture.
DRY RUN — nothing was deleted. These would be moved to Trash:
DRY RUN complete: 45 file(s), 2.63 GB would be freed.
```

File count before and after: 941 and 941.

### 3.1 / 3.4 — what the pipeline actually looks like

Counts come from the catalogue, so the view is instant on 116k photos. On-disk
counts are walked only for the small staging folders, where drift is worth
seeing:

```
INTAKE     AA_RAW                0
STAGING    AB_TO_SORT          988   (941 on disk — drift flagged)
STAGING    AC_SORTED             0
ARCHIVE    Ordered by Dates 42,805   1.5 TB
PORTFOLIO  Portfolio         4,527   201 GB
```

RAW health, previously discoverable only by hand:

```
2,516  RAW files
1,063  orphan RAW — derivative already culled
   44  unstacked RAW+JPEG pairs
    0  pictures in RAW/ with no RAW
```

### 3.3 — new imports inherit the portfolio's vocabulary

`/semantic/suggest-tags-bulk` runs the Phase 2 kNN across a whole folder in one
query and adds a folder-level rollup, so an obvious subject for a whole shoot
can be accepted at once. On untagged photos in the staging folder it proposed
the existing hierarchical tags, not generic labels:

```
32 photos  <collection root>
13 photos  <collection root>.Snakes
 9 photos  <collection root>.Snakes.Tantilla
 7 photos  <collection root>.Geckos
 5 photos  Wildlife.Amphibians.Narrow_mouthed_Toads__Microhylidae_
 4 photos  Wildlife.Arthropods.Jumping_Spiders__Salticidae_
```

This is where Phases 2 and 3 meet: 4,523 curated tagged portfolio photos are the
densest labelled region in the library, so new wildlife imports land near them.

### 3.5 — promoting a keeper carries its whole stack

Selecting a JPEG moves every member of its stack, because moving a derivative
without its RAW silently breaks the pair. `photo_stacks.album_path` is rewritten
too — it is half of a unique key with `stem_key`, so leaving it stale would make
the next stack rebuild create a duplicate at the old location.

**A bug caught before it ran:** the first version flattened everything into the
destination root, which would have dumped RAWs out of the `<album>/RAW/`
convention that `move_raws_to_folders` exists to enforce. A file from a RAW
subfolder now lands in one. That rule is `promote_destination()` and is covered
by `backend/tests/test_promote_destination.py`.

Dry run on a real stacked photo:

```
PICKED  _DSC5630.jpg -> Portfolio/Snakes/_DSC5630.jpg
CARRIED _DSC5630.NEF -> Portfolio/Snakes/RAW/_DSC5630.NEF
```

---

## Phase 4 — Make the cull loop exist · DONE

73 rated out of 116,232 was a broken path, not a preference: rating required
opening a photo and clicking. digiKam's loop is arrow through, press 1-5,
press X.

| # | Change | Status |
|---|---|---|
| 4.1 | `1`-`5` rate, `0` clear, `X` reject, `U` clear label, `Ctrl+A`, `Esc` deselect | DONE |
| 4.2 | Shift-click range select in grid | DONE |
| 4.3 | Remove the 250 ms artificial click delay | DONE — 33 ms selection latency |

Shortcuts act on the whole selection and update the grid optimistically before
the write lands, so holding a rating key down the page feels instant. They are
suppressed inside inputs, in review mode, on the map, and while the lightbox is
open, so they never eat typing.

fernKam has no pick-label column, so `X` sets the red colour label — already the
first label everywhere else in the UI — rather than inventing a column for it.

Verified end to end in the browser: selecting a tile and pressing `3` posted to
`/api/photos/batch-edit` and the rated count went 73 -> 74 in the database.
(Test write reverted.)

**Follow-up fix — Review Mode stuck on "Decoding…".** The loading state trusted
the `<img>` load event alone, and that event does not always arrive: a cached
image can already be `complete` before the listener runs, and a failed image
fires `error` instead. Both left the banner up forever with the verdict keys
disabled — and because it is the *fast* files that get cached, the symptom
showed up on JPEGs rather than the slow RAWs the banner was built for. The
state now also reads `el.complete && el.naturalWidth > 0` after each src
change, and a failed image gets a visible "Could not display <file>" with the
controls left usable so the photo can be rated or skipped.

Verified: 6 forward + 6 back over cached JPEGs and 25 rapid-fire navigations,
zero stuck; a dispatched `error` shows the failure state with stars enabled.

Deleting was the most reliable way to trigger it, and the reason is the index
clamp: removing the last photo steps `reviewIdx` *backwards* onto a photo
already viewed, so every delete at the end of a list lands on a cached image.
Re-verified after the fix with 8 deletes at the end of a 64-photo list (trash
endpoint stubbed, no files touched): zero stuck, clamp correct each time.

**Continuous zoom (follow-up).** Review Mode offered only fit or 1:1, and the
lightbox's wheel zoom was gated behind ctrl/cmd — the container is
`overflow-hidden`, so an unmodified wheel had nothing to scroll and the zoom was
simply undiscoverable. Culling wildlife needs the range between those two
presets: 1:1 on an 8256px frame shows a corner, fit shows a thumbnail, and
feather or eye detail lives in between.

Plain wheel now zooms in both. Review Mode sizes the image to
`naturalWidth * zoom` so the existing scroll container keeps working as the pan
surface, and adjusts `scrollLeft/Top` on each step to keep the pixel under the
cursor fixed — without that, every step anchors top-left and throws away
whatever you were looking at. Zoom resets per photo, since carrying 400% onto
the next frame means landing on a corner of a photo you have not seen.

Verified: 100% -> 201% on five wheel steps (1.15^5), down to 28%, reset to 100%
on navigate; lightbox scale(1) -> 2.44 -> 0.41 on plain wheel.

Drag-panning used to be disabled whenever fit mode was on; it now depends on
whether the image actually overflows, which is what decides if there is
anything to pan. A `0` binding for zoom-reset was written and then deleted —
`0` is already "clear rating" and the new branch ran first, so it would have
silently stolen it.

**A second bug that search turned up.** `fetch` rejects only on network
failure, so `fetch(...).then(r => r.json())` treated an HTTP 500 as success and
handed back the error body typed as the success shape. `reviewTrash`'s
try/catch therefore never fired on a failed delete: the photo was dropped from
the review list while still present in the library, with nothing reported. The
destructive calls (photo trash, tag delete, tag-from-photos, face delete, face
batch-delete, photo-tag delete) now go through a shared `okJson`/`okVoid` that
throws on non-2xx. Verified against a stubbed 500: the photo is kept, the count
does not move, and "Failed to trash: simulated server failure" is shown.

---

## Phase 5 — Polish · DONE

| # | Change | Result | Status |
|---|---|---|---|
| 5.1 | Replace every `alert()`/`confirm()` with an in-app dialog | **39 calls across 13 files, 0 left** | DONE |
| 5.2 | Right-click context menus | grid menu with rate/reject/reveal/similar | DONE |
| 5.3 | Reveal in File Explorer | `POST /photos/{id}/reveal` | DONE |
| 5.4 | Visible error states on silent `try/finally` sites | **20 catch blocks added** | DONE |

### 5.1 — the loudest "this is a web page" tell

digiKam ships 833 modal dialogs, so modals were never the problem; boxes titled
`localhost:5173 says:` were. `lib/dialog.svelte.ts` exports `notify()` and
`ask()`, shaped to swap one-for-one with the natives, rendered by a single
`<AppDialog />` mounted in the root layout. `ask()` is async because a real
dialog cannot block the event loop the way `window.confirm` does — every call
site was already inside an async function, which the typechecker confirmed.

`notify()` collapses duplicate messages. When the backend is down every page's
loader fails at once, and eleven identical dialogs stacked behind one another is
worse than no error at all. Confirmations are never collapsed — each one is a
distinct decision.

### 5.2 / 5.3 — right-click, the idiom fernKam was missing

digiKam uses a context menu in 35 files; fernKam had none. Right-clicking a tile
outside the current selection selects just that photo first, matching every file
manager. The menu offers Open, Find visually similar (straight into the Phase 2
index), the 1-5 ratings, Reject, and Reveal in File Explorer.

Reveal shells out per platform. `explorer /select,` returns exit code 1 even on
success, so its return code is deliberately not checked.

### 5.4 — failures that used to be invisible

20 sites ran `try { ... } finally { loading = false }` with no `catch`: on
failure the spinner stopped and nothing said why. Each now surfaces the error.
One of them, `loadPeople()`, returns its last-known list from the catch so
callers still receive a defined array — without that the added catch changed the
return type and broke two call sites, which the typechecker caught.

---

## Setup from scratch, and a correctness pass · DONE

The library had only ever been built incrementally on one machine, so a fresh install had never
actually been tried. Trying it found three separate blockers, each fatal on its own:

1. `alembic upgrade head` ignored `backend/.env` and used a password hardcoded in
   `alembic.ini`. `alembic/env.py` only read `os.environ`, never the `.env` file.
2. Migration `0016` created `ix_photos_exif_gin` without `IF NOT EXISTS`, and `0001`/`0011`
   had already created it. **The migration chain had never run on an empty database.**
3. `scripts/init-db.sql` left `CREATE EXTENSION vector` commented out. pgvector is not a
   trusted extension, so migration `0025`, running as `fernkam_user`, died with
   `permission denied`.

Now: `docker compose up -d` (PostgreSQL 17 + pgvector 0.8.6, extensions created on first
start) or a native server, then `uv run fernkam setup-db [--docker]`. That creates the role,
database and extensions, writes `.env`, and migrates. Verified from nothing on both paths, plus
the server's own startup auto-migration on an empty database. Backups gained a
`PG_DOCKER_CONTAINER` mode, so the Docker path needs no host `pg_dump`. Native restores no
longer fail on superuser-owned extensions.

Bugs found and fixed along the way, each reproduced first:

| | Effect | Measured |
|---|---|---|
| Photo sorts had no `id` tiebreaker, though the keyset cursor assumed one | pages skipped/repeated photos | 3,000 rows, `rating_desc` cursor: **2,074 missing, 601 repeated → 0 / 0** |
| XMP write-back didn't record the file's new mtime | next scan re-read every written file and **reverted in-app edits** made since | rating 5 set after a write-back came back as 3 → stays 5 |
| Scan deleted rows for folders it could not read, or for an unplugged drive | tags/faces/ratings lost | empty root: 54 rows kept, reason shown |
| Task guard check-then-insert wasn't atomic; Cancel released it while work continued | two file jobs at once | double-click → exactly one 409 |
| Library scan ignored Cancel | — | stops between batches, nothing removed |
| *Sync stack tags* used the server's pooled engine from another event loop | workflow failed, even in Preview | `got Future attached to a different loop` → runs |
| Workflow output capture swapped the process-wide `sys.stdout` | other prints leaked in; overlap could silence the server | captured per thread |
| Face detection held a DB connection through decode + GPU queue | pool pressure during scans | peak idle-in-transaction on 4 cores: 4 → 2 (scales with CPU count) |
| Every video was fully transcoded with libx264, even browser-playable MP4 | a CPU core per view, no seeking | H.264 MP4 served directly with Range (47 ms) |
| API bound to `0.0.0.0` with `allow_origins=["*"]` and no auth | any web page could POST `/api/sync/reset-db` | cross-site and DNS-rebinding writes → 403 |

---

## Files as the shared truth · DONE

fernKam is the main place to tag and organise, but other programs still touch the same files.
Before this, a file changed elsewhere kept its old thumbnail and search entry, and a rescan
could silently overwrite in-app edits that had not been written back yet.

| | Now |
|---|---|
| Outside edits while fernKam is closed | a scan on startup reads them in (`SCAN_ON_STARTUP`) |
| Outside edits while it runs | a folder watcher scans the changed folder a few seconds later (`WATCH_LIBRARY`) |
| File and fernKam both changed a field | three-way merge against the last-synced snapshot; the file wins, the replaced value is kept for **Restore** on the *Changed outside fernKam* page |
| Pixels edited | thumbnail rebuilt, faces and the CLIP embedding reset |
| File moved or renamed | same row kept (matched by sha256), tags/faces/ratings survive |
| Write-back | checks for outside edits first; per-file results, so only files that failed are retried |
| Import from a folder outside the library | refused; scans stay inside `LIBRARY_ROOT` |
| Selected photos | right-click → *Reread metadata from file* / *Write metadata to file* |

Metadata goes into the files themselves (embedded XMP/EXIF). No sidecar files are written.

Found while building it: the watcher's AnyIO worker threads are non-daemon, which kept the
server process from exiting on shutdown. It now runs in its own daemon thread.

---

## Tag Review: tags learn like faces · DONE (first stage)

Phase 2's suggestions learned from every tag in the library, including the ones nobody had
checked. Tags from files and digiKam are mostly right, but "mostly" is what a model amplifies.
Now tags work like faces:

| | |
|---|---|
| Every existing tag | starts **unverified** (`photo_tags.verified_at IS NULL`) |
| Tag Review page | per tag: *To verify*, *Suggestions*, *Approved*, *Rejected*; click the wrong ones, Enter approves the rest |
| Rejecting | removes the tag from the photo (and the file, at the next write-back) and keeps it as a negative example |
| Learning | one logistic regression per tag on the CLIP embedding, from approved tags and rejections only; starts at 8 approved photos, retrains every 10 decisions |
| Accuracy | cross-validated recall and agreement with your decisions, plus the live share of suggestions you accepted |
| Discover kNN | approved neighbours only |

Three things the first version got wrong, each caught by the end-to-end test before shipping:

1. **Under-confident small tags.** With 10 approved photos the model ranked correctly but
   scored almost nothing above 0.5: 10% recall, no suggestions. Fixed by calibrating on the
   cross-validation output (Platt scaling).
2. **The weak-negative sample hides positives.** An untagged heron that lands in the random
   "probably not a heron" sample is learned as a negative, so the model can no longer find the
   photos most worth suggesting. Those photos are now scored by the fold that never saw them.
3. **Balanced odds flood a big library.** Calibrated as if half of all photos had the tag, the
   0.5 bar let through about 4% of everything: thousands of wrong suggestions at 116k photos.
   Library-wide scores now add the tag's prevalence (Bayes prior shift).

On synthetic embeddings (a 312-photo test library: 40 tagged herons, 5 wrong tags, 20 untagged
herons), after approving 10 and rejecting 2: the 3 remaining wrong tags sort first under *Most
doubtful*, and 16 suggestions are 16 real herons. After the rest of the decisions, all 20 are
found with no wrong suggestions. In the unit test, 20 rejections of a look-alike class cut its
false acceptance from 92% to 4%.

### Stage 2: several models, a species specialist, a second opinion · DONE

| | |
|---|---|
| SigLIP 2 (so400m / base) | downloaded as ONNX from onnx-community, indexed into `photo_embeddings` |
| BioCLIP 2 | exported once from the official weights (`uv run --with open-clip-torch fernkam export-model bioclip2`), checked against PyTorch before it is kept |
| Per-tag ensemble | one expert per model, non-negative stacking per tag and per combination of available models |
| Local vision model | Ollama (or any OpenAI-compatible server) answers yes/no per suggestion; shown, sortable, measured against your decisions, never a label |
| Find by name | text towers rank photos for a tag before it has approved photos |

The export path was checked for real with open_clip's ViT-B/16 and ViT-L/14 (BioCLIP 2's
architecture), with random weights since this environment cannot download checkpoints: onnxruntime
reproduced PyTorch exactly (cosine 1.000 on images and text), and fernKam's own preprocessing
matched open_clip's after aligning torchvision's resize and crop rounding.

On synthetic vectors where egrets look like herons to the general model:

| | general model alone | with the species model |
|---|---|---|
| egrets accepted as herons (unit test, new photos) | 20% | **0%** |
| weight the ensemble gives it, per tag | | species 0.50 vs general 0.13; noise model 0.00 |
| end to end, 312-photo library, round 1 | | 16/20 untagged herons suggested, 0 egrets |
| end to end, after 40 approvals | | 18/20, 0 egrets |

Two more fixes found by testing:
- Photos without a species vector at first scored low, because "missing" counted as a
  neutral vote. Now there is one stacker per combination of models, so they are judged by the
  models they have.
- On a small library the random negative sample held most untagged herons, and recall of
  them stalled at 65%. A second training pass that keeps only reliable negatives brought it
  to 94% in simulation, with look-alikes still kept out.

### Stage 3: where and when · DONE

| | |
|---|---|
| Place expert | closeness to the library's photo clusters (30 km and 300 km scales), learned per tag |
| Season expert | day of year (two harmonics) and time of day |
| Range expert (GBIF) | a linked species' share of its class's records around each place, per month; red *not here* / amber *rare* badges |

On synthetic data where herons (Florida, winter) and egrets (Maine, summer) look the same
to the image model: egrets accepted as herons 61% → **0%** with place and season, herons
still found at 99%. End to end with a fake GBIF: all five "heron in Norway in January" photos
flagged *not here* and sorted first under *Most doubtful*; 20/20 untagged herons suggested,
0 egrets.

Found by the end-to-end test: the reliable-negative pass (stage 2) let context decide which
random photos were hidden positives, so ordinary photos at the heron's place in season
dropped out of the negatives and were suggested (8 of them; 1–7.5% in simulation). That call
is now made on appearance alone (0–0.5%), with a unit test that fails under the old rule.

For a 24 GB GPU the recommended set is SigLIP 2 at 512 px (small, distant subjects), BioCLIP 2,
and `qwen3-vl:32b` in Ollama for the double-check (`qwen3-vl:8b` when speed matters).

Not built yet: using SigLIP 2 for Discover's text search.

### Installed on the real machine (RTX 3090)

Merged into main after the live upgrade: migrations 0027–0031 applied, then the startup
refresh ran over 118,265 files: 118,263 unchanged, **2 moves matched by SHA-256** (tags kept),
5 really gone, 0 errors. The cross-site guard returned 403 for foreign and `null` origins
and 404 (passed) for localhost, and the LAN could not connect.

| Model | State |
|---|---|
| SigLIP 2 512 px + text tower | downloaded |
| BioCLIP 2 | exported. onnxruntime matched PyTorch (cos ≥ 0.999) |
| `qwen3-vl:32b` | pulled (20 GB), auto-picked over the older vision models |

**Found on first index: batch 32 at 512 px does not fit in 24 GB.** SigLIP's attention at
1,024 tokens is ~2 GB per layer at that batch. VRAM filled (23.9 of 24.5 GB), Windows spilled
to system RAM, and indexing fell to **1 photo/s (a 26-hour ETA)**. Batch size is now per
model: 8 at 512 px and 16 at 384 px, keeping batch × tokens² about constant.
`FERNKAM_EMBED_BATCH` overrides it for smaller cards. Measured after the change: **7.5 photos/s**
at first (32 → 704 in 90 s). **That didn't last.** After 1,856 photos VRAM was full again
(24.1 of 24.5 GB) and the speed was back to 1 photo/s. A smaller batch only postponed the
problem. Two causes, both fixed:

- **Every new batch size cost memory for the rest of the run.** Unreadable photos are dropped
  from a chunk before inference, so batch sizes varied. onnxruntime keeps a buffer plan (and
  cuDNN a benchmarked workspace) for each input shape it sees. Batches are now padded to one
  fixed shape. The test counts the shapes the session receives: 4 before, 1 after.
- **Cancelling never gave the GPU back.** The release sat on the success path only, so a
  cancelled or failed index kept ~20 GB until fernKam closed. Anything run next, BioCLIP 2 or
  a vision check, would have crawled too. It is released in a `finally` now.

With both fixes the exe ran at **10 photos/s**, but the worker still grew in 2 and 4 GB steps
(15.3 → 17.3 → 21.4 GB in 25 minutes). A third cause, found by measuring on the real model:

- **Every thread that calls `run()` gets its own GPU memory pool, kept forever.** asyncio's
  default executor hands successive batches to up to 16 threads. SigLIP 2 at 512 px on one
  thread held flat at 6,390 MB. Each new thread added **+2,056 MB**, so 6 threads reached
  18,738 MB. All CLIP and Tag Review model inference now runs on one dedicated thread
  (`clip_embed.run_session`). The same test with a new thread per call stayed flat at
  **6,414 MB** and went from 7.5 to 9.1 img/s. `test_embed_models` fails if inference ever runs
  on more than one thread.

Separately, the GPU was only ~47% busy even at full speed, because decoding and inference take
turns. Running them in parallel is the next ~2×. 93% of the `lg` thumbnails it reads were
already cached.

In the full run with that fix, SigLIP 2 finished at 10–11 photos/s with the worker flat at
7,004 MB. BioCLIP 2 ran at 46–47 photos/s and reached 102,080 of 118,265 before fernKam was
closed for other GPU work. "Index the rest" resumes it.

### Living with it

- **Closing no longer flashes the log.** On X, the launcher's watchdog saw the backend it had
  just killed, took it for a crash, and loaded the error page with the log tail while the
  window was closing. It now stands down when the launcher is the one closing. The window waits
  until the server's port is closed, so the UI closes last: port closed at 1,575 ms, window at
  1,577 ms, no processes left.
- **digiKam and MariaDB are gone, and so is the import code.** The user uninstalled digiKam.
  Removed: the MariaDB importer, `import-digikam`, `verify`, `preflight --digikam`,
  `MYSQL_URL`, the `pymysql` dependency, and a digiKam-era face-region stripping script.
  Migration 0032 drops the four digiKam ID columns, which were empty on the live catalogue.
  Metadata writing is unchanged: ratings, labels, captions, `HierarchicalSubject` tags and
  MWG face regions still go into the files in the format digiKam and Lightroom read.
  `fernkam preflight` also crashed on its first ✓ in a legacy-code-page console, and now
  prints in UTF-8 like the server.
- **The app's Shutdown button now closes the same way the X does.** It used to call
  `/api/shutdown`, which killed the backend worker. Granian then exited with code 1, the same
  as a crash, so the launcher showed "Backend stopped" and the window stayed up. The button now
  calls the launcher through pywebview's bridge (`window.pywebview.api.quit`), which stops the
  backend, waits for its port to close, then closes the window. Tested in the exe by clicking
  Shutdown → Confirm: port closed at 2,193 ms, window at 2,194 ms, nothing left running.
- **Culling zoom:** every photo in Review opens at fit. Double-click goes to 75% at that spot,
  a click goes on to 100%, and double-clicking a zoomed photo returns to fit. A click that ends
  a drag-to-pan doesn't count. The point under the cursor stays put: on an 8256×5504 frame the
  cursor pixel went 5602,2202 → 5602,2199 → 5602,2199. The anchoring now waits for Svelte's
  `tick()` instead of `requestAnimationFrame`, which never fires while the window is covered.
- **No more leaked exiftool.** Each session's `exiftool -stay_open` outlived the forced stop
  (5 orphans after three days). It now runs in a Windows job that dies with the backend.
  Checked: 0 exiftool processes after closing.
- **The watcher scans every 10 minutes, not seconds after a change.** Copying a card into
  AA_RAW had started a scan after every quiet moment of the copy. Changes still collect as
  they happen, but they're scanned at most once per interval, counted from the first unscanned
  change. It's set in Settings (0 = off) and saved in `app_settings`.

---

## The shoot pipeline, automated

The real flow after a Nikon shoot:

1. Nikon Transfer 2 copies to `AA_RAW/<shoot>`.
2. DxO PureRAW makes a JPG per NEF in `jpg/`.
3. Cull by deleting JPGs.
4. Remove non-keep RAW.
5. Move by hand to SORT ME, then Portfolio or Sorting to `AC_SORTED`, then by hand to `Ordered by Dates`, then by hand to event folders.

Phones join at SORT ME. Measured on the library: the two latest shoots were 277 frames but only
129 moments (148 frames came within 1 s of the previous one), even without the 20 fps bird
bursts. There are 24,608 photos in event folders against 43,311 left in `Ordered by Dates`.

### Done · Remove non-keep RAW no longer threatens Portfolio

It looked for a RAW's picture only in the RAW's own folder and its `jpg/`. Portfolio keeps
RAWs in `Album/RAW/` beside `Album/x.jpg`. A dry run on Portfolio listed **3,236 of 3,239 RAWs
(181 GB)** for the Recycle Bin. Now it lists 173 (5 GB) that truly have no picture.

### Done · Develop with DxO PureRAW, no window, no clicks

PureRAW has no documented command line. Its Lightroom plugin has one, found in the plugin's
compiled Lua and verified live:
`PureRAWv6.exe --as-lightroom-last-settings-plugin --lr-version=14.5 --batch-file <list>`.

- It processes with plugin mode's own last settings, set once with
  `--as-lightroom-preview-plugin`, deletes the list, and exits.
- It needs `--lr-version`; without it, a "Sorry!" dialog.
- It ignores the subfolder setting, so fernKam moves each JPG into `jpg/`.
- **One 46 MP NEF: 27–31 s end to end**, including PureRAW starting.

What the workflow adds:

- **Blank folder means fresh shoots only.** A fresh shoot has no JPGs yet and nothing copied in
  for 2 minutes. A shoot mid-cull is skipped, so deleted JPGs never come back.
- **Every output is checked.** Anything that isn't `<RAW name>.jpg` stops the run with a
  report, since it means PureRAW's profile changed.
- **It's both a file job and a GPU job.** Remove non-keep RAW can't run mid-develop and bin
  NEFs whose JPG isn't written yet, and indexing takes turns with it.
- **After each scan, fresh shoots can be developed automatically** (Settings). Each shoot is
  tried once per session.

**First real run, 2026-09-26: auto-develop is now off by default.** On an 845-NEF shoot, plugin
mode's "last settings" had reverted to PureRAW's default preset: *DeepPRIME 3 – DNG, renamed*.
Yesterday's test runs had used the JPG profile. The log points the plugin at a
`Preferences.ini` that doesn't exist, so those settings aren't reliably stored. The drift check
only ran once PureRAW exited. The stall watchdog, counting only JPGs as progress, killed it
after 15 min and **209 DNGs (38.6 GB)**, which were then binned. The run now stops at the first
file that isn't `<RAW name>.jpg`. The user runs PureRAW by hand for now, with the button kept
for later.

### Done · Finish shoot, Sorting into Ordered by Dates, bursts

- **Finish shoot** replaces "delete JPGs, run Remove, move to SORT ME, move again":
  - Deleted or rejected (red) means the RAW+JPG pair goes to the Recycle Bin.
  - Keepers go to the shoot's destination. In Review Mode, **P** (green) sends a photo to
    Portfolio and **L** (blue) to the client folder instead.
  - The client folder is outside the library. The JPG leaves the catalogue and the RAW is filed
    by date.
  - Moves are atomic per keeper and never overwrite. The scan afterwards matches moved files
    to their rows by content, so tags follow.
  - Dry run on the two live shoots: 163 and 114 pairs filed, 5 videos left for Sorting.
  - **Suggest** uses CLIP neighbours of up to 40 of the shoot's JPGs. For the frog shoot:
    Portfolio, 82% of neighbours, `Portfolio/Toads/Eastern Narrow-Mouthed Toad`, 0.2 s. For
    the product shoot: 0% Portfolio.
- **Sorting** now *moves* straight into `Ordered by Dates/YYYY/MM`, dated by exiftool
  (videos included). A file with no camera date stays in SORT ME and is listed. That replaces
  AC_SORTED's check, so AC_SORTED is no longer used. On the live folders: 5 camera videos, all
  dated.
- **Bursts:**
  - Frames within 1 s in one folder are a moment. `SubSecTimeOriginal` separates frames within
    the same second.
  - They're ranked by the sharpest tile's Laplacian variance. In the test, a sharp subject on a
    smooth sky scored 6,309× the same frame slightly blurred.
  - Live: 163 frames became 65 moments (largest bursts 23 and 16) in 1.1 s; 114 became 64 in 0.7 s.
  - `FocusShiftShooting` is read from the NEF, because PureRAW's JPGs drop it. Those frames stay
    together, unranked.

### Planned, agreed with the user

- **Portfolio suggestion with BioCLIP** instead of CLIP, for species-level folders.
- **Event suggestions for `Ordered by Dates`**, learned from the 24,608 filed event photos
  with the Tag Review ensemble. The season expert would catch birthdays.
- **Ideas strip in the cull** (suggested crops from qwen3-vl, a few looks), as previews only
  and only for the best frame of each burst. Extra JPG files would clutter the shoot, and a
  `-crop.jpg` would count as a keeper for Remove non-keep RAW.

---

## Where this ended up

Every phase in this roadmap is implemented.

| | before | after |
|---|---|---|
| no-op rescan | 7,632 s | **11.8 s** |
| database size | 34 GB | **1,775 MB** |
| catalogue query during a thumbnail burst | 19.19 ms | **2.55 ms** |
| photo embeddings | 0 | **116,106** |
| semantic / text search | none | working |
| tag suggestions from your own taxonomy | none | working |
| face clusters | 1,267 (largest 1,041, junk) | **452 (largest 112, cohesion 0.629)** |
| destructive workflows with a preview | 0 of 4 | **4 of 4** |
| `alert()`/`confirm()` | 39 | **0** |
| right-click menus | 0 | grid |
| grid rating shortcuts | none | `1`-`5`, `X`, `Ctrl+A` |

What was deliberately not built is unchanged and listed under **Not building**:
undo/`audit_log`, a develop module, drag-and-drop folder organisation, a plugin
system, auth, mobile, cloud sync, light table.

---

## Not building

Each of these is a real digiKam feature. Each is wrong here.

| | Why not |
|---|---|
| Undo / `audit_log` | 0 rows, never requested. digiKam spends 27 files on it. The table was dropped in 0.3. |
| A develop/edit module | Lightroom exists and is better. Never compete with it. |
| Drag & drop folder organisation | Category error on a tag-based library, and Phase 2 moves away from folders. |
| Plugin system | digiKam has ~100 DPlugins. fernKam has one user. |
| Auth / multi-user / sharing | One machine, one person. |
| Mobile app, cloud sync | Same. |
| Light table, batch queue manager | Lightroom. |

---

## Why continue fernKam rather than restart or extend digiKam

The expensive parts were already built: ingest of 116k files, a face pipeline
with adaptive thresholds, duplicate detection, RAW stacking, hierarchical
`ltree` tags. Everything in this roadmap compounded on that rather than
replacing it — and the two largest wins (a 647x faster rescan, a 19 GB smaller
database) came from deleting code, not adding it.

digiKam still cannot follow: zero ANN code in its entire source, and CUDA in
exactly one path. fernKam now runs CLIP over 116k photos and pgvector HNSW over
both faces and images, on hardware digiKam leaves idle.
