# fernKam Workflows

What each action in fernKam does to the **database** and to **your files**, in plain terms.
Keep this updated as features change.

The short version:

- **The catalogue is PostgreSQL. Your photos are never modified unless you ask.** Tagging,
  rating, labelling, captioning and naming faces change the database only. They flag the photo
  as "needs sync".
- **Writing to files is explicit.** Metadata is written into the files only by
  **Maintenance → DB → Files** (or a stack's *Sync tags*). Files are moved or deleted only by
  the Workflows page, Duplicates auto-clean, promote-to-portfolio and Trash. All of these
  preview first or ask for confirmation.
- **Deleting means the Recycle Bin.** Trash and duplicate auto-clean use the system trash, and
  the catalogue row is kept but hidden (`status = 0`).
- **Only one job that moves or removes files runs at a time.** A second library scan or file
  workflow is refused (HTTP 409) until the first has actually stopped. Searching, face work and
  indexing keep running alongside.

Long jobs run in the background. The status bar and the **Tasks** page show progress, and most
jobs can be cancelled between batches.

---

## Quick Scan / Import Photos

**User action:** Home → **Quick Scan** (whole library), or **Import Photos** with a folder path.
Also Maintenance → **Rescan Library**. The folder must be inside `LIBRARY_ROOT`, because photos
are catalogued by their path relative to it.

**What happens:**
1. Walks the folder tree and compares each media file with the catalogue by path and
   modification time:
   - **New file**: reads its metadata with exiftool (date, camera, lens, GPS, rating, label,
     title, caption, dimensions). It restores tags from XMP `HierarchicalSubject` and named face
     regions from XMP (e.g. written by digiKam or by fernKam earlier), computes a SHA-256, and
     writes four thumbnail sizes to the disk cache. Videos get a poster frame and a duration
     via ffmpeg.
   - **Changed file** (mtime differs from the one recorded at last sync by more than 2 s):
     re-reads its metadata into the catalogue.
   - **Unchanged file**: skipped without opening it. A no-op rescan of ~116k files takes
     seconds.
   - **File no longer on disk**: its catalogue row is deleted, together with its tags, faces
     and rating. Safeguards: rows under folders that could not be read are kept, nothing is
     deleted if the walk found no media at all (drive unplugged, empty `LIBRARY_ROOT`), and a
     row whose path changed during the scan is not deleted.
2. Face detection runs on each new image as soon as its batch is saved, then one auto-confirm
   sweep runs at the end (see *Face detection*).
3. The task reports imported / refreshed / removed / unchanged counts, and a warning if
   removals were skipped.

**Cancel:** stops after the current batch. Photos imported so far are kept, and nothing is
removed.

| Database | Files | Disk reads |
|---|---|---|
| Adds, refreshes and removes photo rows; adds tags/faces from XMP | none (thumbnails go to `backend/data/thumbnails`) | new and changed files only |

---

## Edit tags, rating, colour label, title, caption, date

**User action:** lightbox, right panel, batch edit bar, keyboard shortcuts (`1`–`5`, `0`, `X`),
Review Mode, *Discover → apply suggested tags*, *Date Inference → apply*.

**What happens:** updates the catalogue and sets `file_sync_dirty` on the photo. Renaming,
moving or deleting a tag, or removing it from all photos, flags every affected photo the same
way.

| Database | Files | Disk reads |
|---|---|---|
| Photo/tag rows; "needs sync" flag | none (deferred to DB → Files) | none |

---

## Face detection

**User action:** runs automatically during a scan; also Maintenance/Face Review "scan unscanned
photos", or detect faces on a single photo.

**What happens:**
1. Decodes the image (RAW files are skipped) and runs InsightFace (`buffalo_l`) on the GPU if
   available, otherwise the CPU. Detections below `MIN_DET_SCORE` or smaller than
   `MIN_FACE_PX` are dropped.
2. Each face's 512-d embedding is compared with pgvector against *ignored* faces (a match there
   means it's ignored too) and against *confirmed* faces:
   - similar enough to a known person → **suggested**, or **confirmed** automatically above the
     threshold set by the sensitivity slider (Settings);
   - otherwise → **unconfirmed**, waiting in Face Review.
3. A 200 px face crop is stored with the face, so review pages never re-read the original.
4. The photo is stamped as scanned, so it isn't scanned again.

| Database | Files | Disk reads |
|---|---|---|
| Face rows (box, embedding, crop, status); photo "scanned" stamp | none | the original image |

## Name, confirm, ignore or delete faces

**User action:** Face Review (per person, or *Unknown faces* clusters), lightbox face overlay.

**What happens:** updates the face's person and status and flags the photo "needs sync".
Confirming faces also refreshes that person's reference centroid and looks for further
unreviewed faces of the same person. **Auto-confirm** sweeps the queue using k-NN votes over
confirmed faces. **Rebuild clusters** groups similar unknown faces so a whole group can be
named at once.

| Database | Files | Disk reads |
|---|---|---|
| Face rows, person centroids, clusters; "needs sync" flag | none | none |

---

## DB → Files (write metadata to files)

**User action:** Maintenance → **DB → Files**. Stack page → *Sync tags* does the same for one
stack's members, after merging their tags, best rating and label.

**What happens:**
1. For every image, collects its tags (plus `People/<name>` for each named face), rating,
   colour label, title, caption and named face regions.
2. Writes them **into the file itself** (exiftool, overwriting in place; no `.xmp` sidecar) in
   batches of 200 with 4 exiftool processes. Fields: `Subject`/`Keywords`,
   `HierarchicalSubject`, `Rating`, `Label`, `Title`, `Description`, and MWG face regions,
   which digiKam reads.
3. Clears the "needs sync" flag and records each file's new modification time, so the next scan
   doesn't mistake the write for an outside edit.

| Database | Files | Disk reads |
|---|---|---|
| "needs sync" cleared; sync time and file mtime recorded | **writes metadata into the originals** | yes |

## Files → DB (refresh metadata from files)

**User action:** Maintenance → **Files → DB**. Use it to pick up edits made in other software
(digiKam, Lightroom…).

**What happens:** re-reads every photo's metadata with exiftool and updates the catalogue
(dates, camera, lens, GPS, dimensions, rating, label, title, caption). Missing files are
skipped. A normal scan already does this for files whose modification time changed; this
forces it for all of them.

| Database | Files | Disk reads |
|---|---|---|
| Photo metadata overwritten from the files | none | every file |

---

## Semantic indexing and Discover

**User action:** Discover → index the library; then search by description, *Find visually
similar*, or review suggested tags.

**What happens:** computes a CLIP embedding for each photo from its 480 px thumbnail (not the
original) and stores it in `photos.embedding_v`. The model (~600 MB) downloads on first use.
Search compares text or photo embeddings against that index. Tag suggestions come from the
tags of a photo's nearest tagged neighbours, and are only applied when you accept them.

| Database | Files | Disk reads |
|---|---|---|
| Embeddings; accepted tags (flagged "needs sync") | none | thumbnails (originals only if a thumbnail is missing) |

---

## Duplicates

**User action:** Duplicates page. **Compute hashes** fills in missing SHA-256s. The list shows
groups of byte-identical files. **Auto-clean** previews a plan, then applies it.

**What happens (auto-clean):** folders are ranked: any hand-organised folder beats the
archive (`DEDUP_ARCHIVE_FOLDER`), which beats the staging folders (`DEDUP_STAGING_FOLDERS`). In
each group, copies in the best-ranked folder present are kept, and copies in lower-ranked
folders are removed. A copy that belongs to a RAW stack, or has confirmed faces the kept copy
lacks, is listed for manual review instead. Groups whose copies all share one rank are left
alone. The keepers receive the best rating, and any label, title or caption they lack. Removed
copies go to the Recycle Bin and are hidden from the catalogue.

| Database | Files | Disk reads |
|---|---|---|
| Keeper metadata merged; removed copies hidden (`status = 0`) | **duplicates moved to the Recycle Bin** | hashing reads whole files |

## Trash a photo

**User action:** lightbox / Review Mode delete.

**What happens:** moves the file to the Recycle Bin and hides the photo (`status = 0`). If the
move fails, the photo stays and the error is shown. The next scan removes the hidden row.

---

## Workflows page

All four default to **Preview** (dry run), which prints exactly what would happen. **Apply**
asks for confirmation first. The first three count as file jobs, so only one runs at a time.

| Workflow | Does |
|---|---|
| Sort videos | Copies videos from the RAW intake / "SORT ME" folders into dated folders under the sorted export root |
| Remove non-keep RAW | Sends RAW files with no matching JPEG/derivative (the shot was culled) to the Recycle Bin |
| Move RAWs to folders | Moves RAW files into a `RAW/` subfolder next to their JPEGs |
| Sync stack tags | Detects RAW+JPEG stacks, then (on Apply) writes merged tags/rating/label into every member's file |

The page also shows the pipeline stages (intake → staging → archive → portfolio) with live
counts, and a RAW/JPEG pair health check. Both are read-only.

## Promote to portfolio

**User action:** select keepers → *Promote to portfolio* (optional subfolder, rating, tags).
Preview first.

**What happens:** moves each selected photo **and the rest of its stack** into the portfolio
folder. RAWs land in a `RAW/` subfolder there. The catalogue path, stack location, rating and
tags are updated. Files already at the destination are skipped. Refused while a scan or file
workflow is running.

| Database | Files | Disk reads |
|---|---|---|
| Paths, stacks, rating, tags updated | **originals moved** | yes |

---

## Maintenance tools

| Action | Does |
|---|---|
| Backfill thumbnails | Generates missing thumbnails (4 sizes) into the disk cache |
| Backfill face crops | Generates missing 200 px face crops from the originals |
| Backfill video duration | Reads missing video durations with ffprobe |
| Archive low-quality faces | Marks unconfirmed faces below `MIN_DET_SCORE` / `MIN_FACE_PX` as ignored |
| VACUUM ANALYZE / REINDEX / Rebuild indexes | Database housekeeping; no effect on files |
| Backup / Restore | `pg_dump` / `pg_restore` of the catalogue to `backend/data/backups/`; thumbnails and models are not included |

---

## Viewing

| What | Where it comes from |
|---|---|
| Grid thumbnails | Disk cache (`backend/data/thumbnails`), generated on first view if missing; the browser caches them for a day |
| Face crops | Stored in the database; larger close-ups are cut from the original |
| Full-size photo | The original file. RAW is shown via its embedded preview, and TIFF is converted to JPEG on the fly |
| Video | The original file when the browser can play it (H.264 MP4), otherwise a live H.264 transcode by ffmpeg |

Browsing never writes to your files.
