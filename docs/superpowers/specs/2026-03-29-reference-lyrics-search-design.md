# Reference Lyrics Search & Relevance Filtering

## Problem

When a track has a remix, "feat." artist, or unusual naming, the automatic lyrics search during job processing often returns irrelevant results (wrong song entirely) or nothing useful. Users must then manually Google lyrics, copy/paste into the "Add Reference Lyrics" modal — introducing formatting errors and friction. Additionally, irrelevant reference lyrics that do get through skew anchor/gap statistics and confuse correction tooling.

## Solution

Three interconnected improvements:

1. **Relevance filtering in the correction pipeline** — automatically reject reference lyrics that don't match the transcription
2. **Search with alternate artist/title** — let users retry the lyrics search with adjusted metadata from the review UI
3. **Better empty state** — when no valid references exist, show the search/paste UI inline in the Reference panel

## Design

### 1. Relevance Filtering in the Correction Pipeline

**Location:** Inside `LyricsCorrector`, after anchor sequences are found but before corrections are applied.

**Per-source relevance score:**
```
relevance = (reference words appearing in anchor sequences) / (total words in reference source)
```

Sources below `MIN_REFERENCE_RELEVANCE` (default TBD — see Section 5) are removed from `reference_lyrics` and excluded from gap/correction processing.

**Applies to all code paths:**
- Initial job processing — irrelevant sources filtered before corrections saved to GCS
- Add lyrics (paste) — pasted text subject to same threshold
- Search lyrics (new endpoint) — results filtered automatically

**Edge case:** If all sources are filtered out, the job proceeds with zero reference sources. The transcription stands as-is with no corrections. The frontend shows the empty state (Section 4).

**Metadata:** Filtered-out sources are logged in correction metadata (source name + score) for debugging but not stored in `reference_lyrics`.

**Override:** The `search-lyrics` endpoint and `add-lyrics` endpoint accept a `force: true` flag to bypass the threshold for user-selected sources. This handles cases where the transcription is poor and a low match % doesn't mean wrong song.

### 2. New Backend Endpoint — Search Lyrics

**Endpoint:** `POST /api/review/{job_id}/search-lyrics`

**Request:**
```json
{
  "artist": "Billie Eilish",
  "title": "What Was I Made For",
  "force_sources": []
}
```

- `artist` and `title`: Used for the lyrics search across all providers
- `force_sources`: Optional list of source names to add regardless of threshold (for the override flow)

**Behavior:**
1. Download current `corrections.json` from GCS
2. Create all standard lyrics providers (Genius, Spotify, Musixmatch, LRCLIB) with provided artist/title
3. Fetch lyrics from all providers
4. Run through the correction pipeline with relevance filtering
5. Sources passing threshold are added to `reference_lyrics`, corrections recomputed
6. Sources in `force_sources` bypass the threshold
7. Upload updated `corrections.json` to GCS

**Response — success (at least one source passed or force-added):**
```json
{
  "status": "success",
  "data": { "...full CorrectionData..." },
  "sources_added": ["genius", "lrclib"],
  "sources_rejected": {
    "spotify": {"relevance": 0.12, "track_name": "What Was I Made For", "artist": "Billie Eilish"},
    "musixmatch": {"relevance": 0.08, "track_name": "What Was I Made For", "artist": "Billie Eilish"}
  }
}
```

**Response — no valid results:**
```json
{
  "status": "no_results",
  "message": "No lyrics sources passed the relevance threshold",
  "sources_rejected": {
    "genius": {"relevance": 0.15, "track_name": "What Was I Made For", "artist": "Billie Eilish"},
    "spotify": {"relevance": 0.09, "track_name": "What Was I Made For", "artist": "Billie Eilish"}
  },
  "sources_not_found": ["lrclib", "musixmatch"]
}
```

### 3. Frontend — Add Reference Lyrics Modal Redesign

The modal gets two tabs: **Search** (default) and **Paste**.

#### Search Tab

- Two input fields pre-populated from job metadata: **Artist** and **Title**
- Helpful tip: "Try removing 'feat.', 'remix', or other extras from the title"
- "Search All Providers" button
- **Loading state:** Spinner with "Searching providers..."
- **Success (sources passed threshold):** Modal closes, new sources appear in Reference Lyrics panel
- **No results state:** Modal stays open, shows rejected sources as selectable cards:
  - Each card shows: provider name, match %, track name/artist found
  - Checkbox on each to select for force-add
  - Providers that returned nothing shown grayed out (no checkbox)
  - Explanation: "These sources were found but had low word match with your transcription. This could mean wrong song — or the transcription may just be inaccurate."
  - "Add Selected" button (disabled until something checked) to force-add
  - "Search Again" button to retry with adjusted terms
  - Suggestion to try different terms or switch to Paste tab

#### Paste Tab

Identical to current modal: Source Name input + Lyrics textarea. No changes to the paste flow — still calls existing `add-lyrics` endpoint, still subject to relevance filtering. If pasted lyrics fail the threshold, the user gets a similar rejection message with option to force-add.

### 4. Empty State — Reference Panel Inline UI

When no valid reference sources exist after job processing (all filtered or none found), the Reference Lyrics panel shows:

1. **Explanation banner:** "No valid reference lyrics found" with context about why (remixes, feat., live versions)
2. **Inline Search/Paste tabbed UI** — same as the modal content but embedded directly in the panel. No "+ New" button click needed.
3. Artist/Title fields pre-populated from job metadata

Once a valid source is added (via search or paste), the panel switches back to the normal view: source buttons + lyrics display + "+ New" button for the modal.

### 5. Empirical Threshold Analysis

**Goal:** Determine the right `MIN_REFERENCE_RELEVANCE` value from real data.

**Method:**
1. Pull ~30 prod jobs from Firestore that have correction data with reference lyrics
2. Bias sampling toward jobs with mixed results (at least one source with low match %) — pure high-match jobs aren't useful for threshold tuning
3. For each job × source, compute: `(reference words in anchor sequences) / (total reference words)`
4. Generate a report with: job ID, source name, match %, first ~2 lines of transcription and reference (for quick visual comparison)
5. User visually classifies each source as "correct song" or "wrong song"
6. Find the threshold that best separates the two groups

**Output:** A concrete `MIN_REFERENCE_RELEVANCE` value backed by data. Stored as a constant in the corrector module, easily tunable later.

**Timing:** Research step early in implementation, before filtering logic is finalized.

## API Changes Summary

| Endpoint | Change |
|----------|--------|
| `POST /api/review/{job_id}/search-lyrics` | **New** — search with alternate artist/title |
| `POST /api/review/{job_id}/add-lyrics` | **Modified** — add `force` flag for threshold bypass |

## Files Likely Modified

### Backend
- `karaoke_gen/lyrics_transcriber/correction/corrector.py` — relevance filtering after anchor finding
- `karaoke_gen/lyrics_transcriber/correction/anchor_sequence.py` — expose per-source word match stats
- `backend/api/routes/review.py` — new `search-lyrics` endpoint, `force` flag on `add-lyrics`
- `karaoke_gen/lyrics_transcriber/correction/operations.py` — new operation for search-and-add flow

### Frontend
- `frontend/components/lyrics-review/modals/AddLyricsModal.tsx` — tabbed redesign with Search/Paste
- `frontend/components/lyrics-review/ReferenceView.tsx` — empty state inline UI
- `frontend/lib/api.ts` — new `searchLyrics()` method
- `frontend/lib/lyrics-review/types.ts` — types for search response

## Out of Scope

- Changing which providers are used during initial job processing
- Provider-specific configuration (API keys, rate limits)
- Lyrics quality beyond relevance (e.g., formatting, completeness)
