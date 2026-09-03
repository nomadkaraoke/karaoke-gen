# Requests Voting Board — Phase 2 Implementation Plan (the automation)

**Session:** 2026-09-03. Picks up `docs/archive/2026-09-03-requests-voting-board-phase2-handoff.md`.
Phase 1 (the public board) shipped v0.219.0 #975. This builds the daily automation.

## Andrew's decisions (2026-09-03, via AskUserQuestion)
1. **Daily trigger:** **noon US Eastern** — Cloud Scheduler `time_zone="America/New_York"`,
   `schedule="0 12 * * *"` (DST-aware). Full afternoon/evening for the picked requester to review.
2. **Trending agent:** **DEFER** — ship the source-agnostic human-vote path first; the trending
   fallback source (karaoke-decide `candidates` + KaraokeNerds) is a separate follow-up PR.
3. **Negative votes:** picker **skips requests with net `vote_count < 0`** (community-rejected). They
   stay on the board but are not auto-made.
4. **Handoff cap:** try up to **5 distinct voters** (each 24h), then **park** the request (`stalled`).

## Architecture (follows existing gen cron pattern)
Cloud Scheduler (OIDC) → `POST /api/internal/<endpoint>` (`require_admin`) → idempotent worker fn.
Mirrors `stale-review-scheduler` / `youtube-queue-scheduler` exactly.

### One free track/day — enforced by an atomic per-UTC-day lock
`daily_community_pick/{YYYY-MM-DD}` doc, created **create-only** (`if_generation_match=0` /
`.create()` → `AlreadyExists` means another run already claimed today → exit). The lock doc carries a
`phase` so a crashed run can be resumed for the SAME day without double-spending:
`claimed → credit_granted → job_created → done` (or `empty`/`skipped`). Each side effect is guarded:
- **grant credit** — guarded by `request.community_credit_granted` flag (set with the grant); skip if set.
- **create job** — guarded by `request.job_id`; if already set, reuse (don't create a 2nd job).
- **advance status** — transactional `open → queued → in_progress` guarded on current status.

### Picker (source-agnostic)
`list_active` already returns open requests ranked by net votes desc, oldest-first. Picker takes the
top with `vote_count >= 0`. Human votes and (future) trending-agent submissions share this one queue.

### Submit the pick as the requester (so the free credit is consumed; job is owned by them)
Reuse the **bulk-worker programmatic pattern** (NOT a fake HTTP Request):
1. `user_service.add_credits(email, 1, reason="community_daily_pick")` (idempotent — guarded).
2. Build `JobCreate` mirroring `search_audio` (default theme via `get_default_theme_id()`,
   youtube upload + brand prefix + `youtube_description_template`, `user_email=requester`,
   `review_mode="auto"`, `backing_preference="auto"`, `audio_search_artist/title`,
   `community_request_id=<req id>`), `job = job_manager.create_job(job_create, is_admin=False)`.
3. `_prepare_theme_for_job` → `audio_search_service.search_async` → store results → `select_best`
   → `_validate_and_prepare_selection` → `worker_service.trigger_audio_download_worker(job_id)`.
Auto-completion is handled by the existing full-auto review pipeline (`review_mode="auto"`).

### 24h ownership handoff (hourly worker)
Find community requests in `in_progress` whose owner hasn't completed the (lyrics) review within 24h
of becoming owner. Reassign the job to the next up-voter (oldest-first, skipping already-tried),
email them, bump `handoff_attempts`. Cap at 5 → set request `stalled`, stop. Community jobs are
**excluded from `stale_review_processor`** (which would otherwise auto-cancel+refund at 48h) via the
`community_request_id` marker — the handoff owns their lifecycle.

### Voter publish-emails
Single hook: `youtube_queue_processor` (all YT publishing flows through the deferred queue). After the
owner's completion email, look up the SongRequest by `job_id`; if it's a community pick, mark it
`published` + store `youtube_url`, then fan out a "a track you voted for is live" email to every
up-voter (excluding the owner, already emailed). Idempotent via `voters_notified` flag.

## New/changed data model (SongRequest — no migration; additive)
`owner_email`, `owner_assigned_at`, `handoff_attempts:int=0`, `attempted_owners:list[str]`,
`community_credit_granted:bool=False`, `voters_notified:bool=False`. `stalled` added to
`RequestStatus`. New `DailyCommunityPick` lock model. Job gains `community_request_id` (state_data).

## Files
- `backend/models/song_request.py` — new fields, `stalled` status, `DailyCommunityPick`.
- `backend/services/song_request_service.py` — `claim_day`, `pick_eligible`, `transition_status`,
  `list_upvoters`, `get_by_job_id`, flag setters, `mark_published`.
- `backend/workers/community_daily_pick.py` — the picker (kill-switch + dry-run).
- `backend/workers/community_handoff.py` — the hourly handoff.
- `backend/workers/youtube_queue_processor.py` — voter fan-out hook.
- `backend/workers/stale_review_processor.py` — skip community jobs.
- `backend/services/{job_notification,email,template}_service.py` — voter-live email.
- `backend/api/routes/internal.py` — two endpoints.
- `backend/config.py` — `community_daily_pick_enabled`, board link in `default_youtube_description`.
- `infrastructure/__main__.py` — two Cloud Scheduler jobs.
- Tests: `backend/tests/test_community_daily_pick.py`, `test_community_handoff.py`,
  `backend/tests/emulator/test_community_pick_service.py`; extend voter-email + stale-review tests.

## Safety / rollout
Everything gated behind `COMMUNITY_DAILY_PICK_ENABLED` (default **off**) so the PR merges dark.
Shadow-run (`dry_run`) logs what it *would* pick before it creates jobs. Respect
[don't make songs with good community versions] — Phase-2-core relies on human submissions (already
auto-corrected); the trending agent (deferred) must generate candidates the careful way.
