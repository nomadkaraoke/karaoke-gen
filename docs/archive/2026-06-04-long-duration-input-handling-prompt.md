# Prompt: Long-Duration Audio Input Handling (duration-based credits + warn/block flow)

**Created:** 2026-06-04
**Author:** Andrew (via Claude session investigating prod incident on job `9a12cf0f`)
**Status:** Design brief for a NEW Claude session. Not yet started.
**Repo:** `karaoke-gen` (`gen`)

---

## How to use this doc

You are a fresh Claude session. This is your starting prompt. Read it fully, then:

1. Run `/startnomad gen long-duration-input-handling` to get an isolated worktree (or continue in an existing one if directed).
2. Use the **brainstorming** skill first — this is a product+architecture feature with real open questions (see § Open Questions). Do NOT jump to code.
3. Produce a plan doc (`docs/archive/YYYY-MM-DD-long-duration-input-handling-plan.md`) before implementing.
4. This spans backend (credits, job creation, duration detection) and frontend (warn/choose modal), and touches i18n (33 locales) — scope accordingly.

---

## Why this exists — the triggering incident

On 2026-06-04 two production error patterns fired, both from a **single job** `9a12cf0f` ("Van - Mashup"):

1. `lyrics-transcription-job` — `RuntimeError: Lyrics transcription timed out after 1200s`
2. `karaoke-backend` — `Document ... cannot be written because its size (1,442,996 bytes) exceeds the maximum allowed size of 1,048,576 bytes` (on both "Error updating job" and "Error saving corrections").

Root cause of both: the input was a **32.6-minute mashup** (`input_duration_seconds: 1957.8`, 422 lyric segments, 3,742 words). That single outlier:
- Pushed AudioShake transcription past the fixed 1200s app timeout (flapped to `failed` twice before a retry succeeded in 438s).
- Produced a ~1.8 MB corrections payload that blew past Firestore's 1 MB/document limit when the user tried to save their review.

### What was already shipped (the band-aids — PR from version 0.174.14)

These two fixes treat the *symptoms* and are already merged/deployed separately from this feature:

- **Firestore doc-size:** `backend/api/routes/jobs.py` `submit_corrections` now stores only a lightweight summary (`state_data.corrected_lyrics = {'corrected_segment_count': N}`) instead of the full segment arrays; full corrections stay in GCS (`corrections_updated.json`). The `complete_review` 0-segment guard was updated to read the summary (backward-compatible with the legacy full-dict shape).
- **Transcription timeout:** `backend/workers/lyrics_worker.py` now scales the outer timeout by audio duration via `compute_transcription_timeout(duration, settings)` — `clamp(floor=1200, duration * 1.0, cap=1700)`, with `probe_audio_duration_seconds()` (ffprobe). Cloud Run Job task timeout was raised 1800→3000s (`infrastructure/modules/cloud_run.py`) with env `TRANSCRIPTION_TIMEOUT_CAP_SECONDS=2700`.

**These are NOT the real fix.** The real product gap is that we accept arbitrarily long inputs with no duration awareness, no cost adjustment, and no user choice. A 32-minute mashup costs us the same 1 credit as a 3-minute song while consuming 10× the GPU/transcription/encoding resources — and can still break in edge cases. That's what THIS feature addresses.

---

## The feature request (Andrew's words, verbatim)

> this probably also highlights the fact that our system needs some kinda limit for input audio duration, and probably some way to detect long duration inputs (anything greater than 10 minutes) and charge more credits for them - perhaps 1 credit per 10 minutes so e.g. a 35 minute track would cost 4 credits. we'll likely need to rethink the job flow to detect the duration as early as possible (ideally before we're even downloaded the audio, if that's even possible based on whichever source was chosen for the audio) and give the user a flow where the system warns them / gives them a choice, e.g. "this is an unusually long track, it will cost <x> credits if you wish to proceed. proceed/cancel buttons" etc.
>
> anything over 1 hour probably ought to be fully blocked.

---

## Goals / requirements

1. **Duration-based credit pricing.** Charge `ceil(duration_minutes / 10)` credits (proposed): ≤10 min = 1 credit, ≤20 min = 2, ≤30 min = 3, 35 min = 4, etc. Confirm the exact boundary semantics with Andrew (see Open Questions).
2. **Early duration detection** — ideally *before* download, based on the chosen audio source. Detect "unusually long" (>10 min) as early as possible.
3. **Warn/choose flow.** When a track exceeds the free threshold, show the user: "This is an unusually long track. It will cost **X credits** to proceed." with **Proceed / Cancel** buttons. Don't silently charge extra.
4. **Hard block over 1 hour.** Inputs >60 min are rejected outright (with a clear message).
5. **Don't regress the common case.** Normal-length songs (the overwhelming majority) must keep the current 1-credit, no-friction flow.

---

## Key technical context & pointers

### Credit system (currently 1 credit, flat)
- `backend/services/user_service.py` → `deduct_credit(email, job_id, reason)` — hardcoded `amount=-1`, `new_balance = current_credits - 1`. **This must become N-credit aware** (e.g. `deduct_credits(email, job_id, count, reason)` with an atomic transaction; keep the single-credit path working).
- Called from `backend/services/job_manager.py:138` during job creation.
- Credit balance lives on the user doc (`credits`), with `credit_transactions` ledger (`CreditTransaction` model, capped at `MAX_CREDIT_TRANSACTIONS`).
- Insufficient-credit handling already exists ("Insufficient credits") — extend for the N-credit case (user may have 2 credits but the track needs 4).

### Where duration is knowable, per audio source
The job-creation entry points are in `backend/api/routes/file_upload.py`:
- `POST /jobs/upload` (401), `POST /jobs/create-with-upload-urls` (1098), `POST /jobs/create-from-url` (1579), `POST /jobs/{id}/uploads-complete` (1331).

Duration availability by source:
- **Search-based (KaraokeNerds / flacfetch):** Search results ALREADY carry duration. See `backend/api/routes/audio_search.py:166` (`duration: Optional[int]  # Maps to duration_seconds in Release`) and `formatted_duration` (184). **This means duration is known at selection time, before download** — the ideal hook point for the warn/choose modal.
- **URL-based (`create-from-url`, YouTube etc.):** Duration may be obtainable from yt-dlp/flacfetch metadata *before* downloading the full audio (yt-dlp returns `duration` in its info dict). Investigate `flacfetch` / `youtube_download_service` metadata probing. Worst case, probe after a metadata-only fetch.
- **Direct file upload:** Duration is only known after the file is uploaded. Probe with `ffprobe` (see `backend/workers/audio_worker.py` `_capture_audio_source_metadata` ~line 760, and the new `probe_audio_duration_seconds()` in `lyrics_worker.py`). May need a pre-flight probe step or to compute duration in the browser (HTML5 audio metadata) before the upload completes, then confirm server-side.

### Where duration is currently computed (too late)
- `backend/workers/audio_worker.py:~793` — `_capture_audio_source_metadata` runs ffprobe and stores `processing_metadata.audio_source.input_duration_seconds`. This happens AFTER download, in the audio worker. The feature needs detection *earlier* (at/just-after job creation, before charging credits and starting workers).

### Frontend
- Job creation / audio selection UI lives in the Next.js app (`frontend/`). The warn/choose modal should appear at the point the user has chosen a source but before the job is created/charged.
- i18n: any new user-facing strings go in `frontend/messages/en.json` and must be translated to all 33 locales (`python frontend/scripts/translate.py --messages-dir frontend/messages --target all`). Backend user-facing messages live in `backend/translations/{locale}.json` (en/es/de).

---

## Suggested flow (to validate during brainstorming)

```
User selects/enters audio source
        │
        ▼
Detect duration as early as possible (search result field / URL metadata / upload probe)
        │
   ┌────┴─────────────────────────────┐
   │ duration > 60 min?               │── yes ──▶ BLOCK: "Tracks over 60 minutes aren't supported."
   └────┬─────────────────────────────┘
        │ no
   ┌────┴─────────────────────────────┐
   │ duration > 10 min (cost > 1)?    │── no ───▶ Normal 1-credit flow (no friction)
   └────┬─────────────────────────────┘
        │ yes
        ▼
Compute cost = ceil(minutes / 10). Does user have enough credits?
        │
   ┌────┴───────────┐
   │ enough?        │── no ──▶ "This X-min track costs N credits; you have M. [Buy credits]"
   └────┬───────────┘
        │ yes
        ▼
Warn/choose modal: "Unusually long track — costs N credits. [Proceed] [Cancel]"
        │ proceed
        ▼
Create job, deduct N credits atomically, start workers
```

---

## Open questions (resolve with Andrew during brainstorming)

1. **Pricing boundaries:** Is it `ceil(minutes/10)` so 0–10 min = 1 credit, 10:01–20:00 = 2, etc.? Or `floor(minutes/10)+1`? Confirm the 35-min = 4-credit example: `ceil(35/10)=4` ✓. What about exactly 10:00, 20:00?
2. **Hard block threshold:** Exactly 60 min, or "over 60"? Is the block a hard product rule or an admin-overridable limit?
3. **Where to charge:** At job creation (current) or reserve-at-creation / settle-later? Long jobs that fail — do we refund? (There's existing `credit_refunded` handling on jobs — check `refund` logic.)
4. **Duration source of truth:** Search-result duration can be wrong/missing. Do we re-validate against ffprobe after download and reconcile the charge (refund/extra-charge) if it differs materially?
5. **Upload flow UX:** For direct uploads, can we get duration client-side (HTML5 `audio.duration`) to drive the modal before the (potentially large) upload, then confirm server-side?
6. **Admin/impersonation & free tiers:** Should admin-created or impersonated jobs bypass duration pricing? Made-for-you / referral / tenant jobs?
7. **Existing limits:** Is there any current max-duration or max-file-size guard anywhere to extend rather than add new?
8. **Resource implications beyond credits:** Should very long tracks also get different worker resource/timeout profiles (the band-aid already scales transcription timeout; audio separation and encoding may also need attention for 30–60 min inputs)?

---

## Testing expectations

- Backend unit tests for the N-credit deduction (atomic, insufficient-balance, ledger entry with correct amount).
- Tests for the duration→cost function (boundaries: 0, 10:00, 10:01, 35:00, 60:00, 60:01).
- Tests for early-detection per source (mock search result duration, URL metadata, upload probe).
- Frontend tests for the warn/choose modal and block state.
- Production E2E (per `docs/TESTING.md`) for the warn flow if feasible.
- Follow `docs/TESTING.md`; run `make test`.

## Reference job for manual testing
- `9a12cf0f` — "Van - Mashup", 1957.8s input, in `nomadkaraoke` Firestore `jobs` collection. GCS: `gs://karaoke-gen-storage-nomadkaraoke/jobs/9a12cf0f/`. Use as a real-world long-input fixture.
