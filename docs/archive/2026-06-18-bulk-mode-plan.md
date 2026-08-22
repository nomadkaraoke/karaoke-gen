# Bulk Mode Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans / subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. TDD throughout: failing test → run → implement → run → commit.

**Goal:** Add a "Bulk Mode" tab to karaoke-gen that submits up to 100 karaoke jobs at once (by text or by album), auto-completing every song with a confident lossless match and parking the rest in the existing audio-selection review queue.

**Architecture:** A new backend `/api/bulk/submit` creates one job per song (1 credit each, gated upfront), then a `bulk_search_worker` Cloud Run Job runs each audio search server-side and either auto-selects (confident lossless) or parks the job in the existing `AWAITING_AUDIO_SELECTION` state. Album mode adds a MusicBrainz release-lookup service; KaraokeNerds availability comes from a new karaoke-decide endpoint. The frontend adds a Bulk Mode UI and reuses the existing `JobCard` → `AudioSearchDialog` for the review queue.

**Tech Stack:** FastAPI + Firestore + GCS + Cloud Run Jobs (gen backend), Next.js + next-intl (gen frontend), httpx (MusicBrainz), karaoke-decide BigQuery catalog.

## Global Constraints

- **Credit gate:** 1 credit/song minimum; check `>= N` before creating any job; admin bypasses (matches existing `job_manager.create_job`).
- **Max batch size:** 100 songs (reject otherwise).
- **i18n:** no hardcoded user-facing strings; add to `frontend/messages/en.json`, run `python frontend/scripts/translate.py --messages-dir frontend/messages --target all` (33 locales). CI fails on missing keys.
- **Versioning:** bump `tool.poetry.version` in `pyproject.toml` (gen) and decide's version for code changes.
- **Summary projection gotcha:** any job field the dashboard reads must be in BOTH `SUMMARY_FIELD_PATHS` (`backend/services/firestore_service.py`) and `_SUMMARY_STATE_DATA_KEYS` (`backend/api/routes/jobs.py`).
- **Infra:** all GCP changes via Pulumi PRs; run `pulumi up` locally before merge.
- **Tests:** `make test` must pass; user-facing features get a production Playwright E2E.
- **MusicBrainz politeness:** `User-Agent: "NomadKaraoke/1.0 (contact@nomadkaraoke.com)"`, ~1 req/s throttle, short cache.
- **No frontend/backend tier-rule divergence:** the backend `pick_auto_selection` rule mirrors `frontend/lib/audio-search-utils.ts` `getSearchConfidence` tier-1; fixture parity test required.

---

## Phase 0 — karaoke-decide: KaraokeNerds availability endpoint (separate repo/PR)

Independent and shippable first. Work in a **new decide worktree** (`/startnomad decide bulk-availability` or `git worktree add`).

### Task 0.1: `POST /api/catalog/check-availability`

**Files:**
- Modify: `karaoke-decide/backend/api/routes/catalog.py` (add route)
- Reuse: `karaoke_decide/services/catalog_lookup.py` / `track_matcher.py` (`_make_key` normalization, in-memory O(1) lookup)
- Test: `karaoke-decide/tests/...` (mirror existing catalog route tests)

**Interfaces:**
- Consumes: existing catalog service singleton + normalized lookup.
- Produces: `POST /api/catalog/check-availability` body `{ "tracks": [{"artist": str, "title": str}] }` (≤100) → `{ "results": [{"artist","title","available": bool, "brands": [str], "brand_count": int}] }`.

- [ ] **Step 1:** Write failing test: posting 2 tracks (one known-present in catalog fixture, one absent) returns `available=true` w/ brands for the first, `available=false` for the second; >100 tracks → 422.
- [ ] **Step 2:** Run test, verify fail.
- [ ] **Step 3:** Implement route: validate size, normalize each `artist/title` via existing `_make_key`, look up in in-memory catalog, map to `{available, brands, brand_count}` (brands from the catalog song's `brands`/`sources`).
- [ ] **Step 4:** Run test, verify pass.
- [ ] **Step 5:** Add API doc note in decide docs if applicable; bump version.
- [ ] **Step 6:** Commit (`feat(catalog): batch karaoke availability check endpoint`).
- [ ] **Step 7:** Ship decide PR (`/shipit` in decide worktree). Record prod URL of endpoint.

---

## Phase 1 — gen backend foundations

### Task 1.1: `batch_id` on Job + summary projection

**Files:**
- Modify: `backend/models/job.py` (add `batch_id: Optional[str] = None` to JobCreate/Job, or rely on `state_data`)
- Modify: `backend/services/firestore_service.py` (`SUMMARY_FIELD_PATHS` += batch_id path)
- Modify: `backend/api/routes/jobs.py` (`_SUMMARY_STATE_DATA_KEYS` += `batch_id` and bulk flags if in state_data)
- Test: `backend/tests/test_*summary*` (regression asserting `batch_id` in `SUMMARY_FIELD_PATHS`)

**Interfaces:**
- Produces: jobs carry `state_data.batch_id`, `state_data.bulk_settings` (`{auto_select_if_lossless, is_private, skip_audio_edit, skip_customization}`), surfaced in job summaries.

- [ ] **Step 1:** Failing test: a job created with `state_data.batch_id="b1"` appears with `batch_id` in its summary projection; assert `"...batch_id"` present in `SUMMARY_FIELD_PATHS`.
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Add field + both projection entries.
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Commit (`feat(jobs): add batch_id to job summary projection`).

### Task 1.2: `pick_auto_selection` tier helper (backend port of tier-1 rule)

**Files:**
- Modify: `backend/services/audio_search_service.py` (add `pick_auto_selection(results) -> Optional[int]`)
- Reference: `frontend/lib/audio-search-utils.ts` (`categorizeResult`, `getBestResult`, `getSearchConfidence`)
- Test: `backend/tests/test_audio_search_auto_selection.py` (new)

**Interfaces:**
- Consumes: stored search-result dicts (with `is_lossless`, `quality_data`, `seeders`, `release_type`, `filename`/`target_file`).
- Produces: `pick_auto_selection(results: list[dict]) -> Optional[int]` — returns index to auto-select **only** when best result is confidently lossless (tier-1: lossless "BEST CHOICE" category, non-vinyl, no filename mismatch); else `None`.

- [ ] **Step 1:** Failing tests (fixtures mirroring frontend cases): (a) lossless 50+ seeders studio album → returns its index; (b) only-lossy results → `None`; (c) lossless but filename mismatch → `None`; (d) only vinyl-rip lossless → `None` (surface noise); (e) empty → `None`.
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Implement the rule (port categorization + best-pick + tier-1 gate). Keep it small and pure.
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Commit (`feat(audio-search): backend tier-1 auto-selection helper`).

---

## Phase 2 — gen MusicBrainz service + album lookup endpoints

### Task 2.1: MusicBrainz service

**Files:**
- Create: `backend/services/musicbrainz_service.py`
- Test: `backend/tests/test_musicbrainz_service.py` (mock httpx responses)

**Interfaces:**
- Produces:
  - `search_artist(name: str) -> list[{mbid,name,disambiguation}]`
  - `get_releases_for_artist(artist_mbid: str) -> list[{release_group_mbid, title, primary_type, secondary_types, first_release_date, canonical_release_mbid}]`
  - `get_release_tracklist(release_mbid: str) -> {release:{mbid,title,...}, tracks:[{position,title,recording_mbid,disambiguation,length_ms,is_extra,extra_reason}]}`
  - `is_extra_track(release_secondary_types, recording_disambiguation, title) -> (bool, reason)`

- [ ] **Step 1:** Failing tests with recorded MusicBrainz JSON fixtures: artist search parse; canonical-release selection (official + primary-type=Album + earliest standard edition over a deluxe); tracklist parse with `length_ms`; `is_extra_track` flags live/remix/bonus via secondary-type, disambiguation, and title regex; conservative (plain studio track → not extra).
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Implement httpx async client (User-Agent, ~1 req/s throttle, small TTL cache), parsing + extras heuristics.
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Commit (`feat(musicbrainz): release lookup + extras detection service`).

### Task 2.2: decide availability client + album lookup routes

**Files:**
- Create: `backend/services/karaokenerds_availability_service.py` (httpx client → decide `check-availability`, graceful degrade to "unknown")
- Create: `backend/api/routes/bulk.py` (album lookup routes; bulk submit added in Phase 3)
- Modify: `backend/api/main.py` (register router) — confirm actual router registration file
- Test: `backend/tests/test_bulk_album_lookup.py`

**Interfaces:**
- Produces (auth-required):
  - `GET /api/bulk/album/artists?q=` → `[{mbid,name,disambiguation}]`
  - `GET /api/bulk/album/releases?artist_mbid=` → release list incl canonical flag
  - `GET /api/bulk/album/tracklist?release_mbid=` → tracklist enriched with `{available,brands}` per track (decide call); on decide failure each track `available=null`.

- [ ] **Step 1:** Failing tests: artists route returns parsed list; tracklist route merges availability; decide-down → `available=null`, tracks still returned.
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Implement client + routes (decide base URL via settings/env; ≤100 tracks per availability call).
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Commit (`feat(bulk): album lookup routes with KaraokeNerds availability`).

---

## Phase 3 — gen bulk submit endpoint + credit gate

### Task 3.1: `POST /api/bulk/submit`

**Files:**
- Modify: `backend/api/routes/bulk.py`
- Reuse: `backend/services/job_manager.py` (`create_job`), `backend/services/user_service.py` (`check_credits`), `backend/services/worker_service.py` (new `trigger_bulk_search_worker` from Phase 4)
- Test: `backend/tests/test_bulk_submit.py`

**Interfaces:**
- Consumes: `pick_auto_selection` (1.2), `trigger_bulk_search_worker` (4.1).
- Produces: `POST /api/bulk/submit` body `{ songs:[{artist,title,display_artist?,display_title?}], settings:{auto_select_if_lossless,is_private,skip_audio_edit,skip_customization} }` → `{ batch_id, job_ids, total }`.

- [ ] **Step 1:** Failing tests: (a) N songs with `credits>=N` → creates N jobs each tagged `batch_id` + bulk settings, returns ids, triggers one bulk-search worker (mock); (b) `credits<N` (non-admin) → 402, **zero jobs created**; (c) >100 → 422; (d) admin bypasses gate.
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Implement: validate; `check_credits` gate; generate `batch_id` (uuid); loop `create_job(auto_download=False, is_private, state_data{batch_id,bulk_settings})`; trigger worker; return.
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Commit (`feat(bulk): submit endpoint with upfront credit gate`).

---

## Phase 4 — gen bulk search worker + Cloud Run Job

### Task 4.1: `bulk_search_worker` + trigger

**Files:**
- Create: `backend/workers/bulk_search_worker.py` (mirror `audio_download_worker.py`)
- Modify: `backend/services/worker_service.py` (add `trigger_bulk_search_worker(batch_id)` via `_trigger_worker_cloud_run_job`-style dispatch; note: keyed by batch_id not job_id — adapt or pass batch_id in payload)
- Modify: `infrastructure/` (Pulumi: add `bulk-search-job` Cloud Run Job)
- Test: `backend/tests/test_bulk_search_worker.py`

**Interfaces:**
- Consumes: `audio_search_service.search_async`, `pick_auto_selection`, `_validate_and_prepare_selection` + `_download_audio_and_trigger_workers` (from `audio_search.py` — extract shared helpers if needed), `trigger_audio_download_worker`.
- Produces: processes all jobs with `state_data.batch_id == batch_id` needing search.

- [ ] **Step 1:** Failing tests: (a) job whose search yields confident-lossless → auto-selected + download worker triggered; (b) job whose search yields only lossy → parked in `AWAITING_AUDIO_SELECTION` with results stored; (c) `NoResultsError` → parked with "no sources" message; (d) `auto_select_if_lossless=false` → always parked even if lossless.
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Implement worker loop (bounded concurrency, e.g. 4); per-job: search → store results → `pick_auto_selection` (respecting batch setting) → auto-select or park. Reuse existing select/download helpers (refactor `audio_search.py` to expose them if currently inline).
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Add Cloud Run Job in Pulumi; `pulumi up` locally (or note for ship step).
- [ ] **Step 6:** Commit (`feat(bulk): server-side search worker with auto-select/park`).

---

## Phase 5 — gen batch progress endpoint

### Task 5.1: `GET /api/bulk/{batch_id}`

**Files:**
- Modify: `backend/api/routes/bulk.py`
- Test: `backend/tests/test_bulk_progress.py`

**Interfaces:**
- Produces: `{ batch_id, total, counts:{searching,awaiting_selection,processing,complete,failed}, jobs:[{job_id,artist,title,status,auto_selected}] }`. Scoped to requesting user (admin sees any).

- [ ] **Step 1:** Failing test: jobs in mixed states under a batch_id summarize correctly; other users can't read another's batch.
- [ ] **Step 2:** Run, fail.
- [ ] **Step 3:** Implement query by `state_data.batch_id` + ownership check.
- [ ] **Step 4:** Run, pass.
- [ ] **Step 5:** Commit (`feat(bulk): batch progress endpoint`).

---

## Phase 6 — gen frontend Bulk Mode UI

### Task 6.1: api client methods + types

**Files:** Modify `frontend/lib/api.ts`; Test `frontend/__tests__/...`
- [ ] Add `bulkSubmit`, `getBulkBatch`, `searchAlbumArtists`, `getAlbumReleases`, `getAlbumTracklist` + types. Commit.

### Task 6.2: Bulk Mode tab + container

**Files:** Create `frontend/components/job/bulk/BulkMode.tsx`; modify the job-submission parent to add the tab next to the guided wizard.
- [ ] Tab switch (Single / Bulk); Bulk has Text/Album sub-tabs. Strings → en.json. Commit.

### Task 6.3: Text mode

**Files:** Create `frontend/components/job/bulk/BulkTextMode.tsx`; Test (Jest).
- [ ] Add/remove artist/title rows (max 100); per-row availability badge (debounced decide check via gen route); selection state. Tests for cap + selection. Commit.

### Task 6.4: Album mode

**Files:** Create `frontend/components/job/bulk/BulkAlbumMode.tsx`; Test (Jest).
- [ ] Artist search → canonical release auto-loaded + edition switcher → tracklist table; default-unchecked for `is_extra` and `available` tracks; badges; re-tick allowed. Tests for default check/uncheck logic. Commit.

### Task 6.5: Batch settings + submit bar

**Files:** Create `frontend/components/job/bulk/BulkBatchSettings.tsx`, `BulkSubmitBar.tsx`; Test (Jest).
- [ ] Four toggles (auto-pick lossless, visibility, skip edit, skip customize); live credit total; submit disabled until `credits>=N`, else buy-credits dialog. Tests for credit-total + gate. Commit.

---

## Phase 7 — gen frontend batch progress + review queue

### Task 7.1: Batch progress + review queue wiring

**Files:** Create `frontend/components/job/bulk/BulkBatchProgress.tsx`; reuse `JobCard` "Select Audio" → `AudioSearchDialog`; link to jobs list filtered by `batch_id`.
- [ ] After submit, show batch progress (poll `getBulkBatch`); parked jobs reachable via existing Select Audio dialog. Strings → en.json. Commit.

---

## Phase 8 — i18n, E2E, docs, ship

- [ ] **8.1** Run `python frontend/scripts/translate.py --messages-dir frontend/messages --target all`; commit locale files.
- [ ] **8.2** Production Playwright E2E (`frontend/e2e/production/bulk-mode.spec.ts`): submit a small **text** batch (2–3 songs) as admin; assert N jobs created with the shared `batch_id` and expected states; assert credit gate blocks when short (separate low-credit user or mocked). Commit.
- [ ] **8.3** Docs: update `docs/API.md` (new routes), `docs/README.md` (feature status), add `docs/LESSONS-LEARNED.md` note re: backend/frontend tier-rule seam. Commit.
- [ ] **8.4** Bump `pyproject.toml` version.
- [ ] **8.5** `make test` green; `/test-review`; `/docs-review`; `/coderabbit`; `/pr` (gen). Ensure decide PR (Phase 0) merged + deployed first so availability works in prod.
- [ ] **8.6** `/shipit` → merge → wait for deploy → verify prod (`pulumi up` applied for the new Cloud Run Job) → run the prod E2E.

---

## Self-review (coverage check)

- Rule #1 (skip community versions) → Phase 0 + 2.2 + 6.3/6.4 default-uncheck. ✓
- Rule #2 (skip extras) → 2.1 `is_extra_track` + 6.4 default-uncheck. ✓
- Rule #3 (skip disliked) → 6.3/6.4 manual untick. ✓
- Auto + review queue → 4.1 auto/park + 7.1 review queue reuse. ✓
- decide BigQuery source → Phase 0. ✓
- 1 credit/song gate → 3.1. ✓
- Canonical release + switch → 2.1 + 6.4. ✓
- Backend batch + closeable tab → 4.1 Cloud Run Job. ✓
- Max 100 → 3.1 + 6.3. ✓
- Summary projection gotcha → 1.1. ✓
- i18n 33 locales → 8.1. ✓
- Production E2E → 8.2. ✓

## Notes for executor

- `_validate_and_prepare_selection` / `_download_audio_and_trigger_workers` currently live in `backend/api/routes/audio_search.py`; if reused by the worker, extract to a service to avoid importing route internals.
- `trigger_bulk_search_worker` is keyed by `batch_id` (not `job_id`); the existing `_trigger_worker_cloud_run_job` passes `job_id` — adapt the dispatch to pass `batch_id` in the worker payload/args.
- Confirm the FastAPI router registration file before adding `bulk.py` (search for where `audio_search` router is included).
- decide base URL for gen → add a setting/env var; default to prod decide.
