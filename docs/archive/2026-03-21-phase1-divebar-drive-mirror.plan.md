# Plan: Phase 1 — Mirror Divebar Google Drive to GCS

**Created:** 2026-03-21
**Branch:** feat/sess-20260320-0015-divebar-mirror-index
**Status:** Draft
**Depends on:** Nothing (foundation phase)
**Blocks:** Phase 3 (cross-reference index), Phase 4 (KJ Controller UI)

## Overview

Mirror the entire [diveBar Karaoke Google Drive](https://drive.google.com/drive/folders/1zxnSZcE03gzy0YVGOdnTrEIi8It_3Wu8) (~84 brand folders, tens of thousands of karaoke tracks in CDG+MP3, MP4, and ZIP formats) to a GCS bucket in the `nomadkaraoke` GCP project. Build a searchable index of all tracks with parsed metadata (artist, title, brand, format, file size). Run daily to sync new content.

### Why

- **Backup:** Community karaoke content is fragile — Drive links break, creators delete folders, accounts get suspended. Having our own mirror ensures we never lose access to tracks we've come to depend on.
- **Performance:** Downloading from GCS to the KJ miniPC will be faster and more reliable than Google Drive, especially for large files.
- **Searchability:** By parsing filenames and building a structured index, we can search across all 84 brands uniformly despite their inconsistent naming conventions.
- **Foundation:** Phases 3 and 4 depend on having a reliable, indexed copy of the Divebar catalog.

## Requirements

- [ ] GCS bucket `nomadkaraoke-divebar-mirror` with all files from the shared Drive
- [ ] Cloud Function (Python 3.12, Gen2) that recursively lists and downloads files from the Drive folder
- [ ] Cloud Scheduler job running daily (e.g., 2 AM ET) to trigger sync
- [ ] Incremental sync — only download new/modified files, skip unchanged
- [ ] Structured index in BigQuery table `karaoke_decide.divebar_catalog` with parsed metadata
- [ ] Handle all file types: `.cdg`, `.mp3`, `.mp4`, `.mkv`, `.avi`, `.webm`, `.zip`
- [ ] Handle Drive shortcuts (most brand folders are shortcuts to other shared folders)
- [ ] Service account with minimal permissions (Drive API read, GCS write, BigQuery write)
- [ ] Pulumi-managed infrastructure (follows existing karaoke-gen patterns)
- [ ] Logging and error reporting for failed downloads

## Technical Approach

### Google Drive API Access

The folder is publicly shared ("anyone with the link can view"). We'll use the Google Drive API v3 with a service account — even though the folder is public, the API requires authentication for listing operations. The service account doesn't need the folder explicitly shared with it for public folders, but we may need to use an API key for unauthenticated listing.

**Decision:** Use a service account with Drive API enabled. If the folder requires explicit share, the user can share it with the SA email.

### File Organization in GCS

```
gs://nomadkaraoke-divebar-mirror/
  files/
    {brand_folder_name}/
      {original_filename}
  index/
    divebar-index-latest.json.gz
    divebar-index-{YYYY-MM-DD}.json.gz
```

Preserve the Drive folder structure (brand → files) in GCS. This keeps the mirror browsable and allows us to attribute files to brands.

### Filename Parsing

Three main patterns observed across 84 brands:

1. **`BRAND_CODE - Artist - Title.ext`** (e.g., `NOMAD-0001 - This Is Me Smiling - Prettier.mp4`)
2. **`Artist - Title - (Brand Tag).ext`** (e.g., `Alice in Chains - Heaven Beside You - (WTF Karaoke).mp4`)
3. **`Artist - Title.ext`** (implicit brand from folder name)

Parser splits on ` - ` delimiter, tries each pattern, falls back to using the full filename as title with brand inferred from parent folder.

### Incremental Sync Strategy

- Store a `sync_state.json` in GCS with `{drive_file_id: {md5, size, gcs_path, synced_at}}`
- On each run, list all Drive files recursively, compare MD5/size with sync state
- Only download files that are new or modified
- Track deleted files (present in sync state but not in Drive listing) — mark as deleted in index but don't remove from GCS (soft delete)

### Scale Considerations

- ~84 brand folders, estimated 20K-50K files, ~500GB-1TB total
- Initial sync will take hours — Cloud Function has 60-min timeout (Gen2)
- **Solution:** Use Cloud Run Job instead of Cloud Function for initial sync (9-hour timeout). Daily incremental syncs use Cloud Function (fast, only new files).
- Rate limit Drive API calls (10 QPS default for service accounts)
- Use batch upload to GCS with resumable uploads for large files

### BigQuery Index Schema

```sql
CREATE TABLE karaoke_decide.divebar_catalog (
  file_id STRING NOT NULL,           -- Google Drive file ID
  brand STRING NOT NULL,             -- Brand folder name (e.g., "WTF Karaoke Videos")
  brand_code STRING,                 -- Extracted brand code (e.g., "WTF", "CKK", "NOMAD")
  artist STRING,                     -- Parsed artist name
  title STRING,                      -- Parsed song title
  disc_id STRING,                    -- Disc/track ID if present
  filename STRING NOT NULL,          -- Original filename
  format STRING NOT NULL,            -- File format (cdg, mp3, mp4, zip, etc.)
  paired_format STRING,              -- For CDG: "cdg+mp3"; for standalone: NULL
  file_size INT64,                   -- File size in bytes
  gcs_path STRING NOT NULL,          -- Full GCS URI
  drive_path STRING NOT NULL,        -- Original Drive path
  drive_md5 STRING,                  -- MD5 from Drive API
  synced_at TIMESTAMP NOT NULL,      -- When this file was last synced
  deleted BOOL DEFAULT FALSE,        -- Soft delete flag
  artist_normalized STRING,          -- Lowercased, stripped for matching
  title_normalized STRING,           -- Lowercased, stripped for matching
)
```

## Implementation Steps

1. [ ] **Create Pulumi module** `modules/divebar_mirror.py`
   - GCS bucket `nomadkaraoke-divebar-mirror`
   - Service account `divebar-mirror` with roles: `storage.objectAdmin` (on bucket), `bigquery.dataEditor` (on dataset)
   - Enable Drive API on project
   - Cloud Scheduler job (daily 2 AM ET)

2. [ ] **Create Cloud Function** `infrastructure/functions/divebar_mirror/`
   - `main.py` — entry point, incremental sync logic
   - `drive_client.py` — Google Drive API wrapper (recursive listing, download, shortcut resolution)
   - `filename_parser.py` — Parse karaoke filenames into artist/title/brand/disc_id
   - `index_builder.py` — Build and upload BigQuery index
   - `requirements.txt` — google-api-python-client, google-cloud-storage, google-cloud-bigquery

3. [ ] **Implement recursive Drive listing**
   - Handle shortcuts (resolve to actual folder, recurse into them)
   - Handle pagination (Drive API returns max 1000 items per page)
   - Return flat list of `{file_id, name, size, md5, mime_type, parent_folder_name, full_path}`

4. [ ] **Implement filename parser**
   - Pattern matching for the 3 naming conventions
   - CDG+MP3 pair detection (same base name, different extensions)
   - Brand code extraction from disc IDs or folder names
   - Normalization (lowercase, strip diacritics, strip special chars) for matching

5. [ ] **Implement incremental sync**
   - Load sync state from GCS
   - Compare Drive listing with sync state
   - Download new/modified files to GCS (resumable uploads)
   - Update sync state
   - Build and upload BigQuery index

6. [ ] **Create Cloud Run Job for initial sync**
   - Same logic as Cloud Function but with longer timeout
   - Can be triggered manually for first full sync

7. [ ] **Add to Pulumi __main__.py**
   - Import and wire up the new module
   - Export bucket name and function URL

8. [ ] **Test with a subset**
   - Test with 2-3 brand folders first
   - Verify filename parsing accuracy
   - Verify incremental sync (run twice, second should be no-op)

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `infrastructure/modules/divebar_mirror.py` | Create | Pulumi module: bucket, SA, scheduler, function |
| `infrastructure/functions/divebar_mirror/main.py` | Create | Cloud Function entry point |
| `infrastructure/functions/divebar_mirror/drive_client.py` | Create | Drive API wrapper |
| `infrastructure/functions/divebar_mirror/filename_parser.py` | Create | Karaoke filename parser |
| `infrastructure/functions/divebar_mirror/index_builder.py` | Create | BigQuery index builder |
| `infrastructure/functions/divebar_mirror/requirements.txt` | Create | Python dependencies |
| `infrastructure/__main__.py` | Modify | Import and wire up divebar_mirror module |
| `infrastructure/config.py` | Modify | Add DIVEBAR_DRIVE_FOLDER_ID constant |

## Testing Strategy

- **Unit tests** for `filename_parser.py` — test all 3 naming patterns + edge cases
- **Unit tests** for `drive_client.py` — mock Drive API responses, test shortcut resolution, pagination
- **Integration test** — run against 2-3 real brand folders, verify GCS files and BigQuery rows
- **Manual testing** — trigger Cloud Function, inspect GCS bucket and BigQuery table

## Open Questions

- [ ] How large is the full dataset? Need to estimate GCS storage costs (~$0.02/GB/month for Standard)
- [ ] Should we store ZIP files as-is or extract them? (CDG+MP3 pairs inside ZIPs)
- [ ] Do we need the actual file content in GCS for Phase 4, or just the index? (KJ Controller could download directly from Drive using the file ID)
- [ ] Should we use Cloud Run Job (longer timeout) or just let the Cloud Function page through in multiple invocations?

## Rollback Plan

- Delete the GCS bucket contents (files are copies, originals safe in Drive)
- Remove BigQuery table rows
- `pulumi destroy` the module resources
- Revert Pulumi code changes
