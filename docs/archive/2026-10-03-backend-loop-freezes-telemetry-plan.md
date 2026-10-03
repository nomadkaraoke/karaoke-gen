# Backend event-loop freezes + degradation telemetry gaps — plan (2026-10-03)

## Evidence (client_events 2026-09-19 → 10-02)
- 164 `banner_reconnecting`, 49 `banner_unavailable`, 1 `banner_waking`, 27 `waveform_slow`, 19 `waveform_failed`. Peak on 09-29/30.
- 34 of 92 episodes hit 2–3 browsers in the same second. Only ~7 of the 28 "unavailable" episodes are near a deploy.
- Signature in the Cloud Run logs: the single `karaoke-backend` instance stops serving for 20–45 s. Every request on it, `/api/health` included, then completes at the same moment.
- What starts a freeze:
  - `/api/internal/workers/screens` + `auto-correct`. 17 of 28 episodes also log "Matplotlib is building the font cache".
  - The 09:00 crons (`process-stale-reviews` + `youtube-backfill`).
  - Once, `/api/users/auth/verify`.
- **`user_email` is always null.** `degradation-events.ts` hard-codes it, so customers can't be told apart from Andrew's tabs or E2E runs.

## Root causes (audit)
1. **Screens worker.** `generate_screens` is an `async def` BackgroundTask on the event loop, and its work is synchronous: GCS style downloads, 4K PIL title/end renders, GCS uploads, `prepare_review_audio_for_job` (FLAC download + ffmpeg ×≤4), the lazy `audio_analysis_service` import (matplotlib), `executor._compute_timing_signals`, and `pre_apply`. It also runs after the response, so under `--cpu-throttling` it is CPU-starved.
2. **Matplotlib font cache.** It is never pre-built in the image, so it is rebuilt (20–40 s of CPU while holding the GIL) on every new instance. `karaoke_gen/instrumental_review/__init__.py` → `waveform.py` imports pyplot at module import time.
3. **`process_stale_reviews`.** An `async def` with zero awaits: ≤1500 job reads, credit refunds, and email sends (SMTP fallback up to 15 s each).
4. **163 route handlers** are `async def` with no `await`, so sync Firestore/GCS/email/Gemini calls run on the loop. Examples: magic-link verify (sync Gemini credit eval ≤60 s plus the welcome email) and magic-link send.
5. **Nothing detects a blocked loop.**

## Decisions (Andrew, 2026-10-03)
- **Screens:** run inline in the Cloud Tasks request and await it, with the heavy synchronous parts in `asyncio.to_thread`. CPU stays allocated, Cloud Tasks still owns retries, and there is no new infra.
- **Safety net:** a loop-lag watchdog (stack dump), a log metric, and an alert.
- **Telemetry:** all four improvements:
  - server-resolved user (email, tenant, admin/internal/test flags);
  - the existing FingerprintJS `visitorId` (`lib/fingerprint.ts`) plus a per-tab id;
  - a `banner_recovered` event with episode duration;
  - `server_loop_stall` events written by the watchdog into `client_events`.
- **Handler sweep:** convert all no-await `async def` routes to `def` (FastAPI threadpool), with an AST guard test in CI.
- **One PR.**

## Changes
### Backend
- `backend/services/loop_watchdog.py` (new):
  - An asyncio heartbeat task ticks every 250 ms; a daemon thread checks it.
  - At 1 s without a tick → log `EVENT_LOOP_STALL started` with the loop thread's stack (`sys._current_frames`). It resamples at 5 s and 15 s, deduping identical stacks.
  - On resume → `EVENT_LOOP_STALL ended duration_ms=…`: WARNING, or ERROR when ≥10 s.
  - Stalls ≥5 s → Firestore `client_events` doc `type=server_loop_stall` with `duration_ms`, top frames, revision and instance id (written from the thread).
  - Started and stopped in the `main.py` lifespan; can be disabled via a setting.
- `internal.py` `/workers/screens` awaits `generate_screens` instead of using a BackgroundTask. It still returns 200 on handled failures, so Cloud Tasks doesn't double-run.
- `screens_worker.py`: offload `load_style_config`'s sync body, `_generate_title_screen`/`_generate_end_screen` renders, `_upload_screens`, `prepare_review_audio_for_job` + the import, and `_apply_countdown_padding_if_needed`'s heavy work.
- `auto_approval/executor.py`: offload `_compute_timing_signals` (its comment already calls it heavy). `pre_apply.ensure_and_pre_apply`: offload the sync GCS/build section.
- `stale_review_processor.py`: rename the body to sync `process_stale_reviews_sync`, keep an `async` wrapper that does `asyncio.to_thread`, and update the cron.
- Matplotlib:
  - `Dockerfile.base`: `ENV MPLCONFIGDIR=/opt/mplconfig` and `RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.font_manager"` after fonts + pip.
  - `waveform.py`: import pyplot lazily.
- Route sweep: `async def` → `def` for every route handler with no await/async-for/async-with. Fix tests that await them.
- `tests/unit/test_no_blocking_async_routes.py`: an AST guard that fails if a route decorated with `@router.<verb>` is `async def` without an await.
- `client_events.py`:
  - Resolve the caller from the bearer token (`get_token_from_request` + `validate_token_full`, the same pattern as `requests_board.optional_user_email`) → `user_email`, `is_admin`. Ignore the client-sent email.
  - `is_internal` (`INTERNAL_EMAIL_DOMAINS`), `is_test` (`is_test_email`), `tenant` (parsed from the page URL host).
  - New fields: `device_fingerprint`, `tab_id`, `episode_id`.
  - New type `banner_recovered`. `server_loop_stall` is server-only (rejected from clients).
- `scripts/client_events_report.py`: summary by type/day/user/browser that excludes admin/internal/test, plus stall correlation.

### Frontend
- `lib/degradation-events.ts`:
  - Send `Authorization: Bearer <karaoke_access_token>` when present.
  - Send the `device_fingerprint` (lazy `getDeviceFingerprint()`, ≤1.5 s wait, then cached), a per-tab `tab_id` (sessionStorage) and an `episode_id`.
  - `user_email` is removed from the body.
- `components/backend-status-banner.tsx`: track the episode (first non-online → back online) and report `banner_recovered` (unthrottled, one per episode) with `duration_ms` and the peak status.

### Infra
- `infrastructure/modules/monitoring.py`:
  - Log metric `backend/event_loop_stalls` on `EVENT_LOOP_STALL ended` at ERROR severity (≥10 s).
  - Alert "Backend event loop frozen ≥10s" when there are ≥2 in 30 min (free tier).

## Testing
- Unit (pytest):
  - Watchdog: a coroutine doing `time.sleep(1.5)` produces a stall with a stack containing that function, and the Firestore writer is called only for ≥5 s stalls.
  - client_events: token resolution, flags, new fields, `banner_recovered` accepted, `server_loop_stall` rejected from clients.
  - Screens endpoint awaits the worker.
  - Loop responsiveness: with heavy helpers patched to `time.sleep(0.5)`, a concurrent ticker keeps ticking during `generate_screens` and `process_stale_reviews`.
  - The AST guard.
  - The existing suites pass after the sweep.
- Jest: degradation-events (auth header, fingerprint, tab id, no email) and banner recovery (one event with a duration).
- Build: `Dockerfile.base` builds and the font cache exists (CI base build).
- Prod verification after deploy:
  - No "Matplotlib is building the font cache" on new instances.
  - Watchdog logs present and quiet.
  - A new client_events doc has `user_email`/fingerprint populated.
  - The report script runs.
  - Run a real job through screens and confirm `/api/health` latency stays low during it.
