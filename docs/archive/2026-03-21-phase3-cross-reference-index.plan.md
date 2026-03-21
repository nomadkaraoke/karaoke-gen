# Plan: Phase 3 — Cross-Reference Index (KN ↔ Divebar)

**Created:** 2026-03-21
**Branch:** feat/sess-20260320-0015-divebar-mirror-index
**Status:** Draft
**Depends on:** Phase 1 (Divebar mirror + index), Phase 2 (KN pipeline with fresh data)
**Blocks:** Phase 4 (KJ Controller UI — cross-reference badges on KN results)

## Overview

Build an automated cross-reference that links KaraokeNerds catalog entries (~275K songs) to Divebar Google Drive files (~20K-50K tracks). This enables the KJ Controller to show "Available in Divebar" badges on KN search results, and powers the unified search in Phase 4.

### Why

- **User experience:** When a KJ searches KaraokeNerds for a song, they should immediately see if it's available as a free community track in the Divebar collection — and download it from there instead of YouTube.
- **Data enrichment:** Divebar files have inconsistent metadata. Cross-referencing with KN (which has curated artist/title/brand data) enriches the Divebar catalog.
- **Deduplication:** Multiple Divebar brands may have the same song. The cross-reference helps identify duplicates.

## Technical Approach

### Matching Strategy

The challenge: KN has structured data (`Artist`, `Title`, `Brands`) while Divebar has filenames parsed heuristically. We need fuzzy matching.

**Three-tier matching:**

1. **Exact normalized match** (highest confidence, ~0.95)
   - Normalize both sides: lowercase, strip diacritics, remove parentheticals like "(Karaoke Version)", strip "the " prefix
   - Match on `(artist_normalized, title_normalized)`
   - Expected to catch ~60-70% of matches

2. **Fuzzy match** (medium confidence, ~0.80)
   - Use trigram similarity or Levenshtein distance
   - Threshold: 85% similarity on combined artist+title string
   - Catches minor spelling differences, abbreviations, punctuation variations
   - Expected to catch ~15-20% more

3. **Brand-assisted match** (high confidence, ~0.90)
   - Some Divebar brands are also listed in KN's `Brands` field
   - Map Divebar folder names to KN brand codes (e.g., "Nomad Karaoke" → "NOMAD")
   - If a Divebar file's brand matches a KN track's brand list AND artist matches, high confidence
   - This is the most reliable for brands that publish to both platforms

### Where to Run Matching

**BigQuery SQL** — both datasets are already there (Phase 1 puts Divebar in BigQuery, Phase 2 refreshes KN).

```sql
-- Exact match
SELECT
  kn.Id AS kn_id,
  db.file_id AS divebar_file_id,
  'exact' AS match_type,
  0.95 AS confidence
FROM karaoke_decide.karaokenerds_raw kn
JOIN karaoke_decide.divebar_catalog db
  ON kn.artist_normalized = db.artist_normalized
  AND kn.title_normalized = db.title_normalized
WHERE db.deleted = FALSE
```

For fuzzy matching, BigQuery has `SOUNDEX()` and we can use UDFs. Or run fuzzy matching in a Cloud Function with Python's `rapidfuzz` library.

### Output Table

```sql
CREATE TABLE karaoke_decide.kn_divebar_xref (
  kn_id INTEGER NOT NULL,              -- KaraokeNerds song ID
  divebar_file_id STRING NOT NULL,     -- Drive file ID
  match_type STRING NOT NULL,          -- 'exact', 'fuzzy', 'brand_assisted'
  confidence FLOAT64 NOT NULL,         -- 0.0 to 1.0
  divebar_brand STRING,                -- Brand folder name
  divebar_format STRING,               -- File format
  divebar_gcs_path STRING,             -- GCS URI for download
  matched_at TIMESTAMP NOT NULL,       -- When this match was created
  verified BOOL DEFAULT FALSE,         -- Manual verification flag
)
```

### Refresh Cadence

- Run matching after each Divebar mirror sync (daily, triggered by Phase 1 completion)
- Cloud Function or BigQuery scheduled query
- Full rebuild each time (WRITE_TRUNCATE) — simpler than incremental for cross-reference

### API Endpoint

Create a lightweight API (Cloud Function or add to karaoke-gen API) that the KJ Controller can query:

```
GET /api/divebar/lookup?kn_ids=123,456,789
→ { "123": [{"file_id": "abc", "format": "mp4", "brand": "WTF Karaoke", "gcs_path": "gs://..."}], ... }

GET /api/divebar/search?q=bohemian+rhapsody
→ [{"file_id": "abc", "brand": "...", "artist": "Queen", "title": "Bohemian Rhapsody", "format": "mp4", "gcs_path": "gs://...", "kn_id": 123}]
```

## Implementation Steps

1. [ ] **Build brand mapping table**
   - Map Divebar folder names → KN brand codes
   - Manual mapping for the 84 brands (many will be community-only, not in KN)
   - Store as a JSON config or BigQuery table

2. [ ] **Implement normalization functions**
   - BigQuery UDFs or Python functions
   - Lowercase, strip diacritics, remove "(Karaoke)", "(KJ Version)", etc.
   - Strip "the " prefix from artist names
   - Handle "feat." / "ft." / "featuring" variations

3. [ ] **Implement exact matching**
   - BigQuery scheduled query joining on normalized fields
   - Write results to `kn_divebar_xref`

4. [ ] **Implement fuzzy matching**
   - Cloud Function using `rapidfuzz` library
   - Read unmatched Divebar files from BigQuery
   - Match against KN catalog with 85% threshold
   - Append to `kn_divebar_xref` with lower confidence

5. [ ] **Implement brand-assisted matching**
   - For Divebar files from mapped brands
   - Cross-reference with KN tracks that list the same brand
   - Higher confidence than pure fuzzy match

6. [ ] **Create API endpoint**
   - Cloud Function `divebar-lookup` or add routes to karaoke-gen API
   - Two endpoints: bulk lookup by KN IDs, and search by query
   - Returns Divebar file info + GCS download URLs

7. [ ] **Create Pulumi module** `modules/divebar_xref.py`
   - BigQuery table and scheduled query
   - Cloud Function for fuzzy matching
   - API Cloud Function (or Cloud Run route)

8. [ ] **Add to Pulumi __main__.py**
   - Wire up module, set dependency on divebar_mirror and kn_data_sync

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `infrastructure/modules/divebar_xref.py` | Create | Pulumi: BigQuery table, scheduled queries, functions |
| `infrastructure/functions/divebar_xref/main.py` | Create | Matching logic (exact + fuzzy) |
| `infrastructure/functions/divebar_xref/normalizer.py` | Create | Text normalization for matching |
| `infrastructure/functions/divebar_xref/brand_mapping.json` | Create | Divebar folder → KN brand code mapping |
| `infrastructure/functions/divebar_lookup/main.py` | Create | API endpoint for KJ Controller |
| `infrastructure/__main__.py` | Modify | Wire up divebar_xref module |

## Testing Strategy

- **Unit tests** for normalization functions (diacritics, parentheticals, prefixes)
- **Unit tests** for matching logic with known song pairs
- **Accuracy assessment** — sample 100 matches, manually verify correctness
- **Coverage report** — what % of Divebar files have KN matches?
- **API tests** — test lookup and search endpoints

## Open Questions

- [ ] What confidence threshold should we use for showing "Available in Divebar" badges? (Suggest ≥0.80)
- [ ] Should we show multiple Divebar versions of the same KN song? (e.g., MP4 from brand A and CDG from brand B)
- [ ] How do we handle CDG+MP3 pairs in the cross-reference? (One match for the pair, or separate entries?)
- [ ] Should the API endpoint be authenticated? (KJ Controller is local network only, but the API would be public Cloud Run)

## Rollback Plan

- Drop `kn_divebar_xref` BigQuery table
- Remove Cloud Functions
- `pulumi destroy` module resources
- Phase 4 falls back to Divebar-only search without KN cross-reference
