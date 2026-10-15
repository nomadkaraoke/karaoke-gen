# gen local stack for E2E — plan (2026-10-15)

Backlog: INBOX "fake gen backend" item → precursor to mobile Phase 1 (Frog #4).
Spec ref: nomad-mobile `docs/superpowers/specs/2026-10-14-mobile-phase0-design.md` §4.1.

## Goal

One command brings up the **real** gen backend on the Firestore + GCS emulators, with
**fake workers** that move jobs through their real states using canned media, a
**test-only scenario API**, and **email-code sign-in**. Web Playwright and mobile Maestro
full-journey tests both run against it (same endpoints).

## Pieces

### 1. `FAKE_WORKERS` mode (backend)
- Setting `fake_workers` (`FAKE_WORKERS=1`). Hard-refused when `is_production()` (startup raises).
- Seam: every `WorkerService.trigger_*` (audio_download, audio, lyrics, screens,
  render_video, video) short-circuits to `backend/services/fake_workers/` when on. Fakes run
  in a daemon thread (own event loop) so they survive sync routes / short-lived loops.
- Each fake writes what the real worker writes (status transitions via `JobManager`,
  `file_urls`, `state_data`, `processing_metadata`) using real tiny media generated once
  with ffmpeg (tone + "vocals" tone, ~20 s), and a `corrections.json` built from the real
  `CorrectionResult` dataclasses (so schema drift breaks the fake's unit test, not E2E).
  - audio_download → `input.audio`, DOWNLOADING, then audio + lyrics.
  - audio → stems (clean / with_backing / vocals / lead / backing), `backing_vocals_analysis`,
    `instrumental_options`, `audio_complete`.
  - lyrics → `lyrics.corrections`, `lyrics_metadata`, `lyrics_complete`, then the real
    `advance_to_screens_if_ready`.
  - screens → GENERATING_SCREENS → title/end PNGs → AWAITING_REVIEW, or straight to
    REVIEW_COMPLETE + render when the scenario says auto-approve.
  - render_video → RENDERING_VIDEO → `videos.with_vocals` → INSTRUMENTAL_SELECTED → video.
  - video → GENERATING_VIDEO → ENCODING → PACKAGING → COMPLETE with `finals.*` and
    `packages.*` (tiny real mp4 / zip) so download-urls works.
- Fake audio search: `get_audio_search_service()` returns a `FakeAudioSearchService`
  (deterministic results for any artist/title).
- Review preview video: stub mp4 instead of an ffmpeg render.
- Skips: encoding-VM warmup, Secret Manager lookups (no ADC needed), DeBounce / ip-api,
  credit-eval Gemini (`CREDIT_EVAL_ENABLED=false`).
- Per-run behaviour (`/api/test/fake-config`): step delay, review mode
  (`human` | `auto`), `fail_at` worker, duration for the duration-confirm path.

### 2. Test-only API `/api/test/*` (router mounted only when `FAKE_WORKERS`)
- `POST /api/test/reset`: wipe the emulator Firestore + bucket objects (keeps seed).
- `GET/PUT /api/test/fake-config`.
- `POST /api/test/scenario {name, email?}`: seeds named states and returns
  `{session_token, email, job_id?}`. Includes `user` (signed in, N credits), `job_awaiting_review`,
  `job_complete`, `job_failed`, `job_awaiting_duration_confirm`.
- `GET /api/test/emails?to=`: captured outbound mail (the console provider records to
  Firestore `test_email_outbox`), so tests read the magic link or the 6-digit code.
- Fake Stripe: when Stripe isn't configured and fake mode is on, checkout returns
  `/api/test/fake-checkout/{session_id}`. That page runs the real webhook handler path
  (`checkout.session.completed`) and redirects to `success_url`.

### 3. Email-code sign-in (production feature, spec §4.1)
- `POST /api/users/auth/send-code {email, locale}` → 202; `POST /api/users/auth/verify-code
  {email, code}` → `VerifyMagicLinkResponse`-compatible
  `{status, session_token, user, credits_granted, credit_status, message}`.
- The code store is generalized from `KjboxPartnerService` (HMAC-hashed, 10 min TTL,
  5 attempts → 429, per-email throttle in a transaction, single-use marker) into a reusable
  `LoginCodeStore(collection prefix)`. kjbox keeps its collections.
- Per-IP send limit (rolling hour). The usual magic-link abuse checks apply (disposable, blocked
  email / IP, signup IP / fingerprint caps for new users). The account is created at verify time.
- Email contains the code **and** a magic link (same login, either works).
- Pepper: `LOGIN_CODE_PEPPER` env, falling back to a value derived from the admin token.

### 4. Tooling
- `scripts/local-stack.sh up|down|status|logs`: emulators + seed + backend (FAKE_WORKERS,
  fixed ports) + optional frontend dev server pointed at it (`BACKEND_URL`). It binds
  0.0.0.0 so the Android emulator (10.0.2.2) and iOS simulator can reach it.
- `frontend/playwright.local-stack.config.ts` + `frontend/e2e/local-stack/*.spec.ts`
  (full journey: sign in → search → create → review lyrics → instrumental → complete →
  download; buy credits via fake checkout; code sign-in; failure + duration-confirm states).
- CI job `local-stack-e2e` (emulator setup from `backend-emulator-tests` + Playwright).
- Docs: TESTING.md placement table + section, DEVELOPMENT.md, API.md (new auth endpoints).

## Testing
- Unit: login-code store, send/verify routes, fake media/corrections builders (validate via
  `CorrectionResult.from_dict`), fake dispatch seam, test router not mounted without the flag,
  startup refusal in production.
- Emulator: fake pipeline drives a job PENDING → COMPLETE with real Firestore/GCS.
- E2E: Playwright local-stack suite (above), also in CI.
