# Bulk Mode — Design

**Date:** 2026-06-18
**Status:** Approved (design); implementation pending
**Author:** Andrew + Claude
**Repos touched:** `karaoke-gen` (primary), `karaoke-decide` (one new endpoint)

## Problem

Andrew frequently wants to make karaoke versions of *whole albums* by an artist he
loves, but:

1. He doesn't want to make a track if a **community version already exists** (he
   normally checks karaokenerds.com manually).
2. He doesn't want the **"extra" tracks** often tacked onto albums (live versions,
   remixes, bonus tracks).
3. He may want to **skip one or two songs** he dislikes.

Today the web UI only creates **one** karaoke job at a time through a 4-step wizard
(Song Info → Choose Audio → Visibility → Customize & Create). Submitting an album
means repeating that wizard ~12–20 times.

## Goal

Add a **"Bulk Mode"** tab to the job-submission flow that lets a user submit up to
**100** jobs at once, with convenience settings that make a popular full-FLAC album
submit with **no further prompts**, while obscure tracks fall into a short
review queue.

## Decisions (locked with Andrew)

1. **Completion model:** *Auto + review queue.* Batch settings auto-complete every
   song they can: auto-pick the audio **only** when there is a confident
   lossless/FLAC match (tier 1), apply visibility, skip edit/customize. Songs
   without a confident lossless match are parked for a quick per-song selection.
2. **KaraokeNerds availability source:** ~~the karaoke-decide BigQuery catalog via a
   new decide endpoint~~ **REVISED during implementation (2026-06-18):** reuse
   karaoke-gen's existing `karaokenerds_service.check_community_versions` scraper
   (ported from kjbox, already in prod) via a new **batch** helper. Rationale: it
   already exists and is tested, it carries the **`is_community` flag** that
   precisely matches rule #1 ("community-*created* version"), it has a 1h cache +
   graceful degradation, and it avoids a cross-repo deploy. decide's catalog is
   BigQuery *search*-based (no clean batch key-lookup) and lacks the community
   distinction. **No karaoke-decide change is needed.** (Flagged to Andrew.)
3. **Credit gate:** **1 credit/song minimum** in both modes. Duration deltas for
   long tracks are charged per-song during processing as today.
4. **Release selection (album mode):** **auto-pick the canonical release**, with a
   "different edition" switcher.
5. **Search orchestration:** **backend batch.** A new endpoint creates one job per
   song, a worker runs each search server-side, auto-selects confident-lossless
   matches and parks the rest in the **existing `AWAITING_AUDIO_SELECTION` state**.
   The tab can be closed; searches continue server-side.

## Non-goals (v1 / YAGNI)

- Per-song customization or per-song visibility overrides (batch-wide only).
- Reordering tracks; saved batch templates.
- Non-album MusicBrainz sources (e.g. arbitrary playlists).
- A "retry all failed" UI (individual job retry already exists).
- A dedicated batch entity/collection (batch state is derived from `batch_id`).

---

## User experience

A new **Bulk Mode** tab sits next to the existing single-song "Create Karaoke
Video" wizard. It has two sub-modes:

### By text
- A repeatable list of artist/title rows (add / remove), max 100.
- Each row gets an inline **KaraokeNerds availability badge** ("✓ community version
  exists — KV, KaraFun") so the user can deselect, but **nothing is auto-unchecked**
  (no album context to trust auto-deselection).

### By album / release
1. **Artist lookup** (MusicBrainz artist search, reusing decide's pattern).
2. **Canonical release auto-picked**; tracklist shown immediately. A "different
   edition" dropdown lets the user switch release (deluxe/remaster/region).
3. **Tracklist table** with a checkbox per track. Default checked, **except:**
   - **Extras** (live/remix/bonus/compilation) start **unchecked** (rule #2).
   - Tracks with an **existing community version** start **unchecked** (rule #1),
     with a badge showing the brands.
   - User can re-tick anything (rule #3 — skip songs you dislike — is just
     unticking).

### Shared: batch settings + submit
- **Batch settings** apply to the whole batch:
  - *Auto-pick best audio when a lossless/FLAC match is found* (the tier-1 rule;
    off → every song goes to the review queue).
  - *Visibility*: Public / Private (batch-wide).
  - *Skip audio edit* (on by default for bulk).
  - *Skip customization* (on by default; theme defaults).
- **Live credit total** ("12 songs = 12 credits; you have 40").
- **Submit** is disabled until `credits >= N`; if short, show the buy-credits
  dialog with the shortfall. **No jobs are created until the gate passes.**

### After submit
- The user lands on a **batch progress** view (counts by state + per-job status).
- Parked songs surface as the existing **"Select Audio"** button on each `JobCard`,
  opening the existing `AudioSearchDialog`. The "review queue" is just a filtered
  view of the batch's `awaiting_audio_selection` jobs.
- Auto-selected songs proceed to completion, skipping edit/customize per settings.

---

## Architecture

### Flow

```
Select songs ──▶ Batch settings ──▶ Credit gate (N credits) ──▶ POST /api/bulk/submit
                                                                       │
                                  creates N jobs (1 credit each), stamps batch_id,
                                  triggers ONE bulk-search Cloud Run Job
                                                                       │
                              bulk_search_worker iterates jobs (bounded concurrency)
                                                                       │
                    ┌──────────────────────────────────────────────────┴──────────────┐
              confident lossless (tier 1)                            no confident lossless
              auto-select + trigger_audio_download_worker          park: AWAITING_AUDIO_SELECTION
                    │                                                                  │
              normal processing                                       Review queue (JobCard "Select
              (skip edit/customize per settings)                      Audio" → AudioSearchDialog)
```

### Backend (karaoke-gen)

**Existing machinery reused (confirmed in research):**
- `POST /api/audio-search/search` (`backend/api/routes/audio_search.py:583`) already
  creates a job, runs `audio_search_service.search_async`, stores results in
  `state_data.audio_search_results` (with `is_lossless` + `quality_data`), and parks
  in `AWAITING_AUDIO_SELECTION` when `auto_download=False`. The bulk worker mirrors
  this per song rather than calling the HTTP endpoint N times.
- `POST /api/audio-search/{job_id}/select` (`:1132`) + `_validate_and_prepare_selection`
  (`:306`) + `_download_audio_and_trigger_workers` (`:372`) — used for the auto path.
- `audio_search_service.select_best(...)` — best-result picker (extend with a
  tier/lossless check).
- `worker_service._trigger_worker_cloud_run_job(job_id, cloud_run_job_name,
  worker_module)` — Cloud Run Job dispatch (survives instance shutdown).
- `made_for_you` (`backend/api/routes/users.py:821` `_handle_made_for_you_order`) is
  the closest precedent: create job → search inline → park in
  `AWAITING_AUDIO_SELECTION`. Bulk is the multi-song, worker-driven version.
- Credits: `user_service.check_credits` / `deduct_credits`; `job_manager.create_job`
  charges 1 credit atomically and raises `InsufficientCreditsError` if short.

**New:**
- `POST /api/bulk/submit` — body: `{ mode, songs: [{artist,title,display_artist?,
  display_title?}], settings: {auto_select_if_lossless, is_private, skip_audio_edit,
  skip_customization} }`. Steps:
  1. Validate count (1..100) and `check_credits(user) >= N` (admin bypass as today).
  2. For each song, `job_manager.create_job(...)` with: display values, theme
     defaults, `auto_download=False`, `is_private`, plus `state_data.batch_id`,
     `state_data.bulk_settings`, `state_data.bulk_auto_select_if_lossless`,
     `state_data.bulk_skip_audio_edit`, `state_data.bulk_skip_customization`.
     (Each create charges 1 credit — this *is* the gate.)
  3. Trigger **one** `bulk-search` Cloud Run Job with the `batch_id`.
  4. Return `{ batch_id, job_ids, total }` quickly.
- `bulk_search_worker` (`backend/workers/bulk_search_worker.py`, new) — takes a
  `batch_id`, queries its jobs needing search, processes them with bounded
  concurrency. Per job:
  - `transition_to_state(SEARCHING_AUDIO)`.
  - `audio_search_service.search_async(artist, title)`; on
    `NoResultsError`/`AudioSearchError` → park in `AWAITING_AUDIO_SELECTION`.
  - store results in `state_data` (same shape as `search_audio`).
  - **tier check** (see below): if confident lossless **and** auto-select enabled →
    `_validate_and_prepare_selection` + `trigger_audio_download_worker`; else
    `transition_to_state(AWAITING_AUDIO_SELECTION)`.
  - Worker is dispatched as a Cloud Run Job (new `bulk-search-job`) so it survives
    API instance scale-down; processes the whole batch in one execution.
- `GET /api/bulk/{batch_id}` — returns counts by state + per-job `{job_id, artist,
  title, status, auto_selected}` for the batch progress UI. Derived by querying jobs
  with `state_data.batch_id == batch_id` (owned by the requesting user; admin sees
  all).

**Tier check (backend port):** a focused helper
`audio_search_service.pick_auto_selection(results) -> Optional[int]` that returns the
index to auto-select **only** when the best result is confidently lossless
(tier-1-equivalent), else `None`. Mirrors the frontend rule in
`frontend/lib/audio-search-utils.ts` (`getSearchConfidence` tier 1 = `BEST CHOICE`
lossless category, no filename mismatch). Backend already has `is_lossless` +
`quality_data`. **Maintenance seam:** two copies of the rule; add a fixture-based
parity test.

**Job model / data:**
- Add `batch_id: str | None` to the Job model (or keep entirely in `state_data`).
  If surfaced to the dashboard, **add `batch_id` to BOTH**:
  - `SUMMARY_FIELD_PATHS` in `backend/services/firestore_service.py`
  - `_SUMMARY_STATE_DATA_KEYS` in `backend/api/routes/jobs.py`
  and add a regression test (known summary-projection gotcha).

### karaoke-decide (one new endpoint)

- `POST /api/catalog/check-availability` — body `{tracks: [{artist,title}]}` →
  `{results: [{artist,title,available,brands,brand_count}]}`. Reuses the existing
  normalized in-memory catalog lookup (`karaoke_decide/services/catalog_lookup.py` /
  `track_matcher.py` `_make_key` normalization). Bounded input size (≤100).
- karaoke-gen calls this server-side (during album tracklist load and text-row
  enrichment). On failure, degrade gracefully: mark availability "unknown", leave
  tracks checked.

### MusicBrainz release lookup (new, in karaoke-gen)

- New service `backend/services/musicbrainz_service.py` (httpx async, `User-Agent:
  "NomadKaraoke/1.0 (contact@nomadkaraoke.com)"`, polite ~1 req/s):
  - `search_artist(name) -> [{mbid,name,disambiguation}]`
  - `get_releases_for_artist(artist_mbid) -> [release-group summaries]` and
    canonical-release resolution (official, primary-type=Album, earliest standard
    edition).
  - `get_release_tracklist(release_mbid) -> {release, tracks: [{position,title,
    recording_mbid,disambiguation,length_ms,is_extra,extra_reason}]}` via
    `/ws/2/release/{mbid}?inc=recordings`.
- **Extras detection** (`is_extra`): release secondary-types
  (`Live`/`Remix`/`Compilation`) + recording `disambiguation` + title regex
  (`(live)`, `(remix)`, `- Remastered`, `(bonus track)`, etc.). Conservative.
- Exposed via `GET /api/bulk/album/artists?q=` and
  `GET /api/bulk/album/releases?artist_mbid=` and
  `GET /api/bulk/album/tracklist?release_mbid=` (or a single grouped route).
  Tracklist response is enriched with KaraokeNerds availability (decide call).

### Frontend (karaoke-gen)

- New tab in the job submission UI (alongside the guided wizard).
- New components under `frontend/components/job/bulk/`:
  - `BulkMode.tsx` (container + tab switch text/album)
  - `BulkTextMode.tsx` (artist/title row list, max 100, availability badges)
  - `BulkAlbumMode.tsx` (artist search → release switcher → tracklist table with
    extras + availability defaults)
  - `BulkBatchSettings.tsx` (the four batch toggles)
  - `BulkSubmitBar.tsx` (live credit total + gated submit)
  - `BulkBatchProgress.tsx` (uses `GET /api/bulk/{batch_id}`)
- **Review queue reuses** existing `JobCard` "Select Audio" → `AudioSearchDialog`
  (`frontend/components/audio-search/AudioSearchDialog.tsx`,
  `api.getAudioSearchResults` / `api.selectAudioResult`). Batch progress links to the
  jobs list filtered by `batch_id`.
- New `api.ts` methods: `bulkSubmit`, `getBulkBatch`, `searchAlbumArtists`,
  `getAlbumReleases`, `getAlbumTracklist`.
- All user-facing strings → `frontend/messages/en.json`, then
  `python frontend/scripts/translate.py --messages-dir frontend/messages
  --target all` (33 locales). decide endpoint returns data, not display strings.

---

## Edge cases

- **Insufficient credits:** gate before any job created; show shortfall. (Admin
  bypasses, as elsewhere.)
- **MusicBrainz / decide unavailable:** album lookup shows an error and falls back to
  text mode; availability marked "unknown" and tracks stay checked.
- **No audio results for a song:** job parks in `AWAITING_AUDIO_SELECTION` with a
  "no automatic sources" message (same as made_for_you) — appears in review queue.
- **Track too long (> 60 min):** that one job fails on duration pricing as today;
  rest of batch unaffected.
- **Duplicate songs in the list:** allowed (user choice); no dedup in v1.
- **Tab closed mid-search:** searches continue (Cloud Run Job); user returns to the
  batch view / dashboard later.
- **Partial create failure** (credit race after gate): stop, report which jobs were
  created; created jobs are valid and proceed.

## Testing strategy

Per `docs/TESTING.md`:
- **Backend unit:** `pick_auto_selection` tier rule (lossless→index, lossy→None,
  filename-mismatch→None); credit-gate math; extras detection; bulk worker
  auto-vs-park branching; `batch_id` summary-projection regression.
- **Backend integration:** `POST /api/bulk/submit` happy path; insufficient-credits
  rejection (no jobs created); `GET /api/bulk/{batch_id}` shape.
- **MusicBrainz service:** mocked-HTTP tests for canonical-release selection +
  tracklist parsing + extras flags.
- **decide:** `check-availability` matching (hit/miss/normalization).
- **Frontend Jest:** selection state, credit-total computation, default
  checked/unchecked logic.
- **Production E2E (Playwright):** submit a small text batch; assert N jobs created
  with correct `batch_id` and states; assert credit gate blocks when short.

## Open maintenance notes

- Backend tier rule duplicates frontend `audio-search-utils.ts` — fixture parity
  test required; revisit if the rule grows.
- MusicBrainz rate limits (1 req/s) — album lookups are interactive and low-volume;
  add simple in-process throttle + short cache.
