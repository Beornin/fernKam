# fernKam Workflows Documentation

This document details all interactive workflows in fernKam, explaining what happens under the hood for each action in business terms. Keep this updated as features are added, modified, or removed.

---

## Import Photos (Custom Path)

**User Action:** Click "Import Photos" on Home page → Enter folder path → Click "Start Import"

**What Happens:**
1. System scans the specified folder and all subfolders for image and video files
2. For each file found:
   - Compares against existing database records to see if it's new or modified
   - If new: extracts metadata (camera info, GPS, date taken, etc.) from the file
   - If modified: updates existing record with new metadata
   - If unchanged: skips the file
3. For new images only: generates 4 thumbnail sizes (small, medium, large, extra-large) and stores them in the database
4. Returns summary showing how many files were added, updated, skipped, or had errors

**Database Changes:**
- New photo records added for new files
- Existing photo records updated for modified files
- Thumbnail records created for new images (4 per image)

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: reads original files to extract metadata and generate thumbnails

---

## Quick Scan (Entire Library)

**User Action:** Click "Quick Scan" on Home page

**What Happens:**
1. System scans the entire configured library folder and all subfolders
2. Same process as Import Photos, but automatically uses the default library path instead of a custom one
3. Finds new files, updates modified files, skips unchanged files
4. Generates thumbnails for new images
5. Returns summary of changes

**Database Changes:**
- New photo records for new files
- Updated photo records for modified files
- Thumbnail records for new images

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: reads all files in library for metadata and thumbnails

---

## DB → File Sync

**User Action:** Navigate to Sync page → Click "Sync to Files"

**What Happens:**
1. System finds up to 100 photos that have unsaved changes (edits made in the app but not yet written to files)
2. For each photo:
   - Collects all tags, star ratings, captions, and face assignments from the database
   - Writes this information to the original image file using XMP metadata format
   - Marks the photo as "synced" in the database
3. Returns summary showing how many photos were successfully synced and how many had errors

**Database Changes:**
- Marks synced photos as "clean" (no pending changes)
- Records timestamp of last sync

**File System Changes:**
- Yes: writes metadata to original image files (as XMP sidecar or embedded XMP)

**Disk Access:**
- Yes: writes to original image files

**When This Happens:**
- User manually triggers this sync operation
- System does NOT automatically write to files during normal editing (changes are deferred)

---

## File → DB Sync

**User Action:** Navigate to Sync page → Click "Sync to DB"

**What Happens:**
1. System finds up to 100 photos to check
2. For each photo:
   - Reads XMP metadata from the original image file
   - Parses tags, ratings, captions, and color labels from the file
   - Updates the database with this information
   - Creates new tag records if needed
3. Returns summary showing how many photos were synced and how many new tags were created

**Database Changes:**
- Updates photo records with metadata from files
- Creates or updates tag records
- Links tags to photos

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: reads metadata from original image files

**When This Happens:**
- User manually triggers this to pick up changes made in other software (digiKam, Lightroom, etc.)

---

## Backfill Thumbnails

**User Action:** Navigate to Sync page → Click "Backfill Thumbnails"

**What Happens:**
1. System finds photos that don't have thumbnails stored in the database yet
2. For each photo (up to 500 at a time):
   - Reads the original image file
   - Generates 4 thumbnail sizes
   - Stores all 4 sizes in the database
3. Returns summary showing how many photos were processed and how many errors occurred

**Database Changes:**
- Creates thumbnail records for processed photos (4 per photo)

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: reads original image files to generate thumbnails

**When This Happens:**
- One-time migration for photos imported before thumbnail caching was added

---

## Backfill Face Crops

**User Action:** Navigate to Sync page → Click "Backfill Crops"

**What Happens:**
1. System finds detected faces that don't have face crops stored in the database yet
2. For each face (up to 500 at a time):
   - Reads the original image file
   - Crops the face region from the image with some padding
   - Resizes the crop to a standard size
   - Stores the crop in the database
3. Returns summary showing how many faces were processed and how many errors occurred

**Database Changes:**
- Updates face records with crop data

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: reads original image files to generate crops

**When This Happens:**
- One-time migration for faces detected before crop caching was added

---

## Edit Photo Tags

**User Action:** Add or remove tags on a photo (via lightbox or tags page)

**What Happens:**
1. System adds or removes the tag association in the database
2. Marks the photo as "needs sync" (changes not yet written to file)
3. Does NOT immediately write to the file

**Database Changes:**
- Creates or deletes tag-to-photo association
- Marks photo as having unsaved changes

**File System Changes:**
- None (deferred to DB→File sync)

**Disk Access:**
- No

**Previous Behavior:**
- Used to immediately write to file after every tag change
- Now defers file writes to explicit sync operation

---

## Edit Photo Rating/Caption

**User Action:** Change star rating, caption, or title (via lightbox)

**What Happens:**
1. System updates the photo record in the database with new values
2. Marks the photo as "needs sync" (changes not yet written to file)
3. Does NOT immediately write to the file

**Database Changes:**
- Updates photo record (rating, caption, title, color label)
- Marks photo as having unsaved changes

**File System Changes:**
- None (deferred to DB→File sync)

**Disk Access:**
- No

**Previous Behavior:**
- Used to immediately write to file after every edit
- Now defers file writes to explicit sync operation

---

## Face Detection

**User Action:** Click "Detect Faces" on a photo or batch detect multiple photos

**What Happens:**
1. System reads the original image file
2. Uses AI to detect all faces in the image
3. For each detected face:
   - Generates a face crop (square image of just the face)
   - Creates a mathematical representation (embedding) of the face
   - Compares the face to known people in the database
   - If a match is found, suggests the person's name
   - Stores the face crop and embedding in the database
4. Creates face records in the database

**Database Changes:**
- Creates face records with crop data and embeddings
- Links faces to people if matches are found

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: reads original image file for detection

**Note:**
- Face crops are stored in the database at detection time, so viewing them later doesn't require disk access

---

## Assign/Unassign Faces

**User Action:** Assign a face to a person, unassign, or delete a face

**What Happens:**
1. System updates the face record in the database
2. Marks the associated photo as "needs sync" (changes not yet written to file)
3. Does NOT immediately write to the file

**Database Changes:**
- Updates face record (person assignment, status)
- Marks photo as having unsaved changes

**File System Changes:**
- None (deferred to DB→File sync)

**Disk Access:**
- No

**Previous Behavior:**
- Used to immediately write face regions to file
- Now defers file writes to explicit sync operation

---

## View Thumbnail

**User Action:** Browse photos grid, view album, etc. (thumbnails load automatically)

**What Happens:**
1. System checks if thumbnail is already stored in the database
2. If cached: returns thumbnail directly from database (no disk access)
3. If not cached:
   - Reads original image file
   - Generates thumbnail
   - Stores in database for future use
   - Returns thumbnail

**Database Changes:**
- If not cached: creates thumbnail record

**File System Changes:**
- None (read-only for fallback)

**Disk Access:**
- Only if thumbnail not cached in database (fallback)
- After first view, all subsequent requests are database-only

**Previous Behavior:**
- Always read from disk cache directory

---

## View Face Crop

**User Action:** View face review page, people page, etc. (face crops load automatically)

**What Happens:**
1. System checks if face crop is already stored in the database
2. If cached: returns crop directly from database (no disk access)
3. If not cached:
   - Reads original image file
   - Crops face region
   - Stores in database for future use
   - Returns crop

**Database Changes:**
- If not cached: updates face record with crop data

**File System Changes:**
- None (read-only for fallback)

**Disk Access:**
- Only if crop not cached in database (fallback)
- After first view, all subsequent requests are database-only

**Previous Behavior:**
- Always read original image and crop on every request

---

## View Original Photo

**User Action:** Open lightbox, play video

**What Happens:**
1. System serves the original file directly from disk

**Database Changes:**
- None

**File System Changes:**
- None (read-only)

**Disk Access:**
- Yes: serves original file (unavoidable for viewing full-size images and videos)

**Note:**
- This is the only remaining disk access during normal browsing
- All thumbnails and face crops are now served from database

---

## Sync Status

**User Action:** Navigate to Sync page (status loads automatically)

**What Happens:**
1. System counts photos with unsaved changes (edits not yet written to files)
2. System counts photos that have never been synced to files
3. System finds the date of the last successful sync
4. Displays this information on the page

**Database Changes:**
- None (read-only)

**File System Changes:**
- None

**Disk Access:**
- No (pure database query)

**Previous Behavior:**
- Used to walk filesystem and check file modification times
- Now is database-only for performance

---

## Architecture Notes

### PostgreSQL as Primary Store
- Thumbnails (4 sizes per photo) are stored in the database
- Face crops are stored in the database
- Metadata (tags, ratings, captions) is stored in the database with a "needs sync" flag

### Disk Access Minimization
- Normal browsing: thumbnails and face crops served from database (no disk access)
- Lightbox/video: original files served from disk (unavoidable)
- Imports: disk read for metadata extraction and thumbnail generation
- Face detection: disk read for detection (batch operation)
- DB→File sync: disk write for metadata (explicit user action)
- File→DB sync: disk read for metadata (explicit user action)

### Deferred File Writes
- Changes made in the app (tags, ratings, faces) are NOT immediately written to files
- Instead, photos are marked as "needs sync"
- User must explicitly trigger DB→File sync to write changes to files
- This allows batching multiple edits before writing to disk

### Backfill Operations
- One-time migration tools for existing data
- Backfill thumbnails: generates database thumbnails for photos imported before caching
- Backfill crops: generates database face crops for faces detected before caching
