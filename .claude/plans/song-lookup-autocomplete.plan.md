# Plan: Song Lookup & Autocomplete + Community Version Detection

**Created:** 2026-03-02
**Branch:** feat/sess-20260302-2324-song-lookup-autocomplete
**Status:** Implementation Complete

## Overview

Add two features to the karaoke-gen job creation flow:

1. **Artist/title autocomplete** using karaoke-decide's MusicBrainz + Spotify catalog data, so users get properly-styled artist/title text (e.g. "Fleetwood Mac" instead of "fleetwood mac")
2. **Community karaoke version detection** that warns users when a high-quality community karaoke version already exists on YouTube (via karaokenerds data), saving them credits and time

Both features are **optional and non-blocking** — users can still type whatever they want and proceed even if a community version exists.

## Requirements

- [ ] Artist field autocomplete across all 3 job creation tabs (Search, Upload, URL)
- [ ] Title field autocomplete (optionally filtered by selected artist) across all 3 tabs
- [ ] Autocomplete uses canonical MusicBrainz/Spotify text styling
- [ ] Autocomplete is optional — users can ignore suggestions and type freely
- [ ] Community version check triggers when both artist and title are filled
- [ ] If community karaoke version exists, show a dismissible banner with YouTube link(s)
- [ ] Community check does not block job submission — just informs the user
- [ ] Graceful degradation if karaoke-decide API or karaokenerds.com is down

## Architecture Decisions

### How to access karaoke-decide catalog data

**Decision: Proxy through karaoke-gen backend**

Both services share the same GCP project (`nomadkaraoke`), but the frontend at `gen.nomadkaraoke.com` can't directly call `decide.nomadkaraoke.com/api/` due to CORS (different origins). Options considered:

| Option | Pros | Cons |
|--------|------|------|
| **Proxy via karaoke-gen backend** | No CORS issues, no changes to karaoke-decide, frontend already calls api.nomadkaraoke.com | Added latency (extra hop), karaoke-decide dependency |
| Direct frontend calls + CORS | Simpler, no proxy code | Requires karaoke-decide CORS changes, cross-project coupling |
| Query BigQuery directly from karaoke-gen | No dependency on karaoke-decide service | Duplicates query logic, BigQuery setup needed, higher latency |

The proxy approach is cleanest: karaoke-gen frontend → karaoke-gen backend → karaoke-decide API. Response caching in the proxy minimizes latency impact.

### How to detect community karaoke versions

**Decision: Scrape karaokenerds.com from karaoke-gen backend** (same approach as kjbox)

The `karaokenerds_raw` BigQuery table has the catalog but NOT community status. Community status is determined by an `<img class="check">` badge in karaokenerds.com's HTML, only available via web scraping. The kjbox codebase has proven, well-tested scraping logic we can port directly.

Search URL: `https://karaokenerds.com/Search?query={artist}+{title}&webFilter=OnlyWeb`

Returns: songs with tracks, each track having `brand_name`, `brand_code`, `youtube_url`, `is_community`.

### Autocomplete UI component

**Decision: Use existing shadcn Command (cmdk) + Popover components**

The frontend already has `command.tsx` and `popover.tsx` from shadcn/ui. The standard combobox pattern (Popover + Command) provides:
- Keyboard navigation (arrow keys, enter to select)
- Debounced search-as-you-type
- Clean dropdown with loading states
- Works inline within forms (not modal)

## Technical Approach

### Data flow

```
User types in artist field
  → debounced (300ms) API call to GET /api/catalog/artists?q=fle
  → karaoke-gen backend proxies to decide.nomadkaraoke.com/api/catalog/artists?q=fle
  → returns [{ name: "Fleetwood Mac", mbid: "...", popularity: 85 }, ...]
  → user selects "Fleetwood Mac" → field populated with canonical name

User types in title field
  → debounced (300ms) API call to GET /api/catalog/tracks?q=dreams&artist=Fleetwood+Mac
  → returns [{ track_name: "Dreams", artist_name: "Fleetwood Mac", popularity: 82 }, ...]
  → user selects "Dreams" → field populated with canonical title

Both fields filled → community check fires
  → POST /api/catalog/community-check { artist: "Fleetwood Mac", title: "Dreams" }
  → backend scrapes karaokenerds.com for "Fleetwood Mac Dreams" with webFilter=OnlyWeb
  → if community tracks found with YouTube URLs, return them
  → frontend shows banner: "A community karaoke version already exists! [Watch on YouTube →]"
```

### Caching strategy

| Endpoint | Cache TTL | Scope |
|----------|-----------|-------|
| Artist search proxy | 5 min | In-memory (backend) |
| Track search proxy | 5 min | In-memory (backend) |
| Community check | 1 hour | In-memory (backend) |

### karaoke-decide API endpoints used

These are all **public, no auth required**:

- `GET /api/catalog/artists?q={query}&limit=10` — MBID-first artist search
  - Returns: `[{ mbid, name, disambiguation, artist_type, tags, spotify_id, popularity, genres }]`
- `GET /api/catalog/tracks?q={query}&limit=10` — Track search (Spotify data)
  - Returns: `[{ track_id, track_name, artist_name, artist_id, popularity, duration_ms, explicit }]`

## Implementation Steps

### Phase 1: Backend — Catalog proxy + community check endpoints

1. [ ] **Add catalog proxy service** — `backend/services/catalog_proxy_service.py`
   - HTTP client (httpx async) calling karaoke-decide API
   - In-memory TTL cache for responses (5 min)
   - Configurable karaoke-decide base URL
   - Graceful error handling (return empty results on failure)

2. [ ] **Port karaokenerds scraping logic** — `backend/services/karaokenerds_service.py`
   - Port `search()`, `parse_results()`, `_parse_tracks()`, `_parse_single_track()`, `_clean_youtube_url()` from `kjbox/kj-controller/karaoke_nerds.py`
   - Add async support (httpx instead of requests)
   - In-memory TTL cache (1 hour)
   - Return only community tracks with YouTube URLs

3. [ ] **Add catalog routes** — `backend/api/routes/catalog.py`
   - `GET /api/catalog/artists?q=&limit=10` — proxy to karaoke-decide
   - `GET /api/catalog/tracks?q=&artist=&limit=10` — proxy to karaoke-decide
   - `POST /api/catalog/community-check` — scrape karaokenerds.com
   - All endpoints require auth (same as job creation)
   - Pydantic request/response models

4. [ ] **Register routes in main.py** — add `catalog.router` to app

5. [ ] **Add config** — `backend/config.py`
   - `karaoke_decide_api_url` setting (default: `https://decide.nomadkaraoke.com`)

6. [ ] **Add `beautifulsoup4` + `httpx` dependencies** to `pyproject.toml` (if not already present)

### Phase 2: Frontend — Autocomplete component

7. [ ] **Create `AutocompleteInput` component** — `frontend/components/ui/autocomplete-input.tsx`
   - Wraps an Input field with a Popover + Command dropdown
   - Props: `value`, `onChange`, `onSelect`, `fetchSuggestions(query) => Promise<Suggestion[]>`, `placeholder`, `disabled`, `renderItem`
   - Debounced search (300ms) triggered on input change
   - Shows loading spinner while fetching
   - Shows "No results" when search returns empty
   - Keyboard navigation (up/down arrows, enter to select, escape to close)
   - Clicking outside closes dropdown
   - When user selects a suggestion, calls `onSelect(suggestion)` and populates input
   - When user types without selecting, uses their raw text (autocomplete is optional)

8. [ ] **Add catalog API functions** — `frontend/lib/api.ts`
   - `searchArtists(query: string, limit?: number)` → `GET /api/catalog/artists`
   - `searchTracks(query: string, artist?: string, limit?: number)` → `GET /api/catalog/tracks`
   - `checkCommunityVersions(artist: string, title: string)` → `POST /api/catalog/community-check`

### Phase 3: Frontend — Integrate autocomplete into job creation

9. [ ] **Replace artist Input fields with AutocompleteInput** in `JobSubmission.tsx`
   - All 3 tabs: `searchArtist`, `uploadArtist`, `youtubeArtist`
   - Fetch suggestions from `api.searchArtists(query)`
   - On select: populate artist field with canonical name
   - Show artist name + optional disambiguation in dropdown items

10. [ ] **Replace title Input fields with AutocompleteInput** in `JobSubmission.tsx`
    - All 3 tabs: `searchTitle`, `uploadTitle`, `youtubeTitle`
    - Fetch suggestions from `api.searchTracks(query, selectedArtist)`
    - On select: populate title field with canonical name
    - Show track name + artist name + duration in dropdown items

### Phase 4: Frontend — Community version detection

11. [ ] **Create `CommunityVersionBanner` component** — `frontend/components/job/CommunityVersionBanner.tsx`
    - Shows when community karaoke version(s) found
    - Displays: "A karaoke version of this song already exists!"
    - Lists community tracks with brand name and YouTube link
    - "Watch on YouTube" button for top community version
    - Dismissible (X button or "Continue anyway" link)
    - Styled with green border (matching kjbox community track styling)

12. [ ] **Add community check logic to `JobSubmission.tsx`** (Search tab only)
    - Debounced check (500ms) when both artist AND title fields have values
    - Only triggers when values change (not on every keystroke)
    - Shows `CommunityVersionBanner` between the form fields and the submit button
    - Does NOT block form submission
    - Clears when artist or title changes

### Phase 5: Testing

13. [ ] **Backend unit tests** — `backend/tests/unit/test_catalog_routes.py`
    - Test proxy endpoints with mocked karaoke-decide responses
    - Test community check with mocked karaokenerds HTML
    - Test error handling (karaoke-decide down, karaokenerds down)
    - Test caching behavior

14. [ ] **Backend unit tests** — `backend/tests/unit/test_karaokenerds_service.py`
    - Port relevant tests from `kjbox/kj-controller/tests/unit/test_karaoke_nerds.py`
    - Test HTML parsing, community detection, YouTube URL cleanup

15. [ ] **Frontend component tests** — `frontend/components/ui/__tests__/autocomplete-input.test.tsx`
    - Test suggestion fetching and display
    - Test keyboard navigation
    - Test selection behavior
    - Test free-text input (no selection)

16. [ ] **E2E tests** — `frontend/e2e/job-creation-autocomplete.spec.ts`
    - Test autocomplete appears on typing
    - Test selecting from autocomplete populates field
    - Test community banner appears for known songs
    - Test form submission works with and without autocomplete selection

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/services/catalog_proxy_service.py` | Create | HTTP client for karaoke-decide API with caching |
| `backend/services/karaokenerds_service.py` | Create | Karaokenerds.com scraper (ported from kjbox) |
| `backend/api/routes/catalog.py` | Create | Proxy endpoints for artist/track search + community check |
| `backend/main.py` | Modify | Register catalog router |
| `backend/config.py` | Modify | Add karaoke_decide_api_url setting |
| `pyproject.toml` | Modify | Add beautifulsoup4 dependency (if missing) |
| `frontend/components/ui/autocomplete-input.tsx` | Create | Reusable autocomplete input component |
| `frontend/components/job/CommunityVersionBanner.tsx` | Create | Community version warning banner |
| `frontend/components/job/JobSubmission.tsx` | Modify | Replace Input with AutocompleteInput, add community check |
| `frontend/lib/api.ts` | Modify | Add catalog API functions |
| `backend/tests/unit/test_catalog_routes.py` | Create | Backend route tests |
| `backend/tests/unit/test_karaokenerds_service.py` | Create | Scraper unit tests |
| `frontend/components/ui/__tests__/autocomplete-input.test.tsx` | Create | Component tests |
| `frontend/e2e/job-creation-autocomplete.spec.ts` | Create | E2E tests |

## Decisions (Resolved)

- **Track search scope**: All Spotify tracks (256M) — better coverage for new/obscure songs
- **Community check scope**: Search tab only (Upload/URL users already have their audio)
- **Rate limiting**: 20 requests/min per user on catalog proxy endpoints (prevents abuse of karaokenerds scraping). All endpoints require auth.
- **"Display As" fields**: No autocomplete — they're for deliberate overrides like soundtrack names
- **CORS**: Proxy approach (no changes needed to karaoke-decide)

## Rollback Plan

- Feature is entirely additive — no existing functionality is modified
- Autocomplete fields fall back to plain text Input if API calls fail
- Community check is non-blocking — if it fails, form works normally
- All new endpoints are behind the existing auth middleware
- Can disable by reverting the frontend changes (backend proxy is harmless if unused)
