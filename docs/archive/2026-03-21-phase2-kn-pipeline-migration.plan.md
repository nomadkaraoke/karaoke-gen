# Plan: Phase 2 — Migrate KaraokeNerds Data Pipeline to nomadkaraoke

**Created:** 2026-03-21
**Branch:** feat/sess-20260320-0015-divebar-mirror-index
**Status:** Draft
**Depends on:** Nothing (can run in parallel with Phase 1)
**Blocks:** Phase 3 (cross-reference index)

## Overview

Migrate the daily KaraokeNerds data export pipeline from the legacy `projectbread-karaokay` GCP project to the `nomadkaraoke` project. Currently, two Gen1 Cloud Functions in `projectbread-karaokay` fetch community and full catalog exports daily from the KN API and store them in `gs://projectbread-karaokay.appspot.com/karaokenerds-data/`. The `karaoke-decide` app already has a BigQuery table `karaokenerds_raw` (275K rows) but it's a one-off dump with no automated refresh.

### Why

- **Consolidation:** All nomadkaraoke infrastructure should be in the `nomadkaraoke` GCP project, Pulumi-managed
- **Automated refresh:** The BigQuery table is stale — it needs daily refresh from the KN API to stay current
- **Foundation:** Phase 3 needs up-to-date KN data to cross-reference with Divebar files
- **Cost:** The `projectbread-karaokay` project is on Firebase free tier but still costs for GCS storage (~7GB cumulative). Better to consolidate billing.

## Current State

### In `projectbread-karaokay`:

| Component | Details |
|-----------|---------|
| **Function 1:** `fetchAndStoreCommunityJson` | Daily 3:05 AM ET, fetches `karaokenerds.com/Data/Community?key=...`, stores gzipped JSON to GCS |
| **Function 2:** `fetchAndStoreFullJson` | Daily 4:30 AM ET, fetches `karaokenerds.com/Data/Songs?key=...`, stores gzipped JSON to GCS |
| **Storage:** | `gs://projectbread-karaokay.appspot.com/karaokenerds-data/{community,full}/` |
| **API Key:** | `27b45f98099645c786b2b23cc35a405a` (env var `KARAOKENERDS_KEY`) |
| **Runtime:** | Node.js 18, Gen1, us-central1 |
| **Data size:** | Community: ~1.9 MB/day, Full: ~7.5 MB/day (gzipped) |

### In `nomadkaraoke` (karaoke-decide):

| Component | Details |
|-----------|---------|
| **BigQuery table:** | `karaoke_decide.karaokenerds_raw` (275K rows, 4 columns: Id, Artist, Title, Brands) |
| **API client:** | `karaoke_decide/services/karaokenerds.py` — `KaraokeNerdsClient.fetch_catalog()` (exists but unused for scheduled imports) |
| **Status:** | One-off dump, no automated refresh, TODO noted in DATA-CATALOG.md |

## Requirements

- [ ] New Cloud Function in `nomadkaraoke` that fetches KN full catalog daily
- [ ] Store raw JSON exports in GCS (`gs://nomadkaraoke-kn-data/`)
- [ ] Automated daily BigQuery refresh of `karaokenerds_raw` table
- [ ] Store KN API key in Secret Manager (not env var)
- [ ] Pulumi-managed infrastructure
- [ ] Keep community export too (useful for Phase 3 — community tracks map to Divebar)
- [ ] Verify karaoke-decide still works after table refresh
- [ ] Plan for decommissioning `projectbread-karaokay` functions (after validation)

## Technical Approach

### Single Cloud Function, Two Modes

Rather than two separate functions, create one function `kn-data-sync` with a `mode` parameter:
- `mode=community` — fetch community export
- `mode=full` — fetch full catalog export + refresh BigQuery

Two Cloud Scheduler jobs trigger the same function with different payloads.

### BigQuery Refresh Strategy

The full catalog is ~275K rows and ~7.5MB gzipped. Small enough to do a full replace:

1. Fetch JSON from KN API
2. Store raw gzipped JSON in GCS (date-stamped + latest pointer)
3. Parse JSON into rows
4. Load to BigQuery staging table via `WRITE_TRUNCATE` (atomic replace)
5. Swap staging → production (or just use `WRITE_TRUNCATE` directly on the main table since it's atomic)

### Enhanced Schema

The current `karaokenerds_raw` schema is minimal (4 columns). The KN API likely returns more fields. We should capture everything available:

```sql
-- Existing (keep for backwards compatibility)
Id INTEGER,
Artist STRING,
Title STRING,
Brands STRING,

-- New fields (from full API export, if available)
youtube_urls STRING,         -- JSON array of YouTube URLs per track
community_tracks STRING,     -- JSON array of community contributions
brand_codes STRING,          -- Comma-separated brand codes
last_updated TIMESTAMP,      -- When KN last updated this entry
```

We'll inspect the actual API response to determine the full schema.

## Implementation Steps

1. [ ] **Inspect KN API response**
   - Fetch `https://karaokenerds.com/Data/Songs?key=...` (use existing key)
   - Document the full JSON schema
   - Determine which fields to capture in BigQuery

2. [ ] **Create Secret Manager secret**
   - Store KN API key as `karaokenerds-api-key` in Secret Manager
   - Grant access to the new service account

3. [ ] **Create Pulumi module** `modules/kn_data_sync.py`
   - GCS bucket `nomadkaraoke-kn-data`
   - Service account `kn-data-sync` with roles: `storage.objectAdmin` (on bucket), `bigquery.dataEditor` (on dataset), `secretmanager.secretAccessor`
   - Cloud Function v2 (Python 3.12)
   - Two Cloud Scheduler jobs (community at 3:05 AM ET, full at 4:30 AM ET)

4. [ ] **Create Cloud Function** `infrastructure/functions/kn_data_sync/`
   - `main.py` — entry point, mode dispatch
   - `fetcher.py` — HTTP fetch from KN API with streaming
   - `bigquery_loader.py` — Parse JSON, load to BigQuery
   - `requirements.txt`

5. [ ] **Update BigQuery table schema**
   - Add new columns (if any) to `karaokenerds_raw` in karaoke-decide Pulumi
   - Or create a new table `karaokenerds_full` with expanded schema alongside the existing one

6. [ ] **Test the pipeline**
   - Run function locally or via direct invocation
   - Verify GCS files created correctly
   - Verify BigQuery table refreshed
   - Verify karaoke-decide app still works (quiz, recommendations)

7. [ ] **Run in parallel with legacy**
   - Keep both karaokay and nomadkaraoke functions running for 1 week
   - Compare outputs to ensure parity

8. [ ] **Decommission legacy**
   - Disable Cloud Scheduler in karaokay (don't delete yet)
   - After 30 days, delete functions and scheduler

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `infrastructure/modules/kn_data_sync.py` | Create | Pulumi: bucket, SA, function, scheduler |
| `infrastructure/functions/kn_data_sync/main.py` | Create | Cloud Function entry point |
| `infrastructure/functions/kn_data_sync/fetcher.py` | Create | KN API HTTP client |
| `infrastructure/functions/kn_data_sync/bigquery_loader.py` | Create | BigQuery load logic |
| `infrastructure/functions/kn_data_sync/requirements.txt` | Create | Dependencies |
| `infrastructure/__main__.py` | Modify | Wire up kn_data_sync module |
| `infrastructure/modules/secrets.py` | Modify | Add karaokenerds-api-key secret |

### Cross-repo changes (karaoke-decide):

| File | Action | Description |
|------|--------|-------------|
| `infrastructure/__main__.py` | Modify | Update BigQuery table schema if needed |

## Testing Strategy

- **Unit tests** for JSON parsing and BigQuery row generation
- **Integration test** — fetch from actual KN API, verify response shape
- **Comparison test** — compare new pipeline output with legacy karaokay export
- **Regression** — verify karaoke-decide quiz/recommendation queries still work after schema change

## Open Questions

- [ ] What is the full KN API response schema? Need to inspect before finalizing BigQuery schema.
- [ ] Does the community export have YouTube URLs that map to Divebar content?
- [ ] Should we keep historical exports (date-stamped) or just latest? Current karaokay keeps all (~1100 days).
- [ ] Should the BigQuery table live in karaoke-gen's infra or karaoke-decide's infra? (Currently in karaoke-decide)

## Rollback Plan

- Disable new Cloud Scheduler jobs
- Re-enable legacy karaokay functions (they're still there)
- BigQuery table can be restored from any historical GCS export
