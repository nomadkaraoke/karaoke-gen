# Long-Duration Input Handling & Duration-Based Credit Pricing — Design

**Date:** 2026-06-04
**Repo:** karaoke-gen
**Status:** Design (approved in brainstorming, pending spec review)
**Author:** Andrew Beveridge (with Claude)

---

## 1. Context & motivation

On 2026-06-04 a single 32-minute mashup (job `9a12cf0f`, "Van - Mashup", 1957.8s / 422
segments) triggered two simultaneous production failures:

- **lyrics-transcription-job** `RuntimeError: timed out after 1200s` (AudioShake transcription
  of 32-min audio exceeded the fixed app timeout; recovered on retry in 438s).
- **karaoke-backend** Firestore `Document size (1,442,996 bytes) exceeds 1,048,576` on
  corrections save (the segment arrays dominate the payload).

Band-aids shipped in v0.174.14 (corrections moved to GCS-only; duration-scaled transcription
timeout). The **real fix** is to treat input duration as a first-class concept: price jobs by
length, detect length as early as possible, confirm cost with the user before doing the
expensive work, and hard-block inputs above a supported ceiling. This also corrects a long-
standing economic problem — AudioShake transcription and GPU separation cost scales with audio
length, but every job currently costs the user a flat **1 credit** regardless of length.

Today: `job_manager.create_job()` checks `has_credits()`, persists the job, then
`deduct_credit()` (amount −1). No duration awareness anywhere.

---

## 2. Goals & non-goals

### Goals
- Charge credits proportional to input duration: `ceil(minutes / 10)` credits.
- Hard-block inputs longer than 60 minutes (the inputs that caused the incident).
- Detect duration as early as each input source allows, and **confirm cost with the user
  before any heavy processing**.
- Reconcile the charge against the *actual* audio length once it is authoritatively known
  (after torrent download, and after any audio edit), since metadata can lie.
- Reuse existing machinery: the ffprobe helper, the flacfetch `/check-youtube` probe, the
  review-stage pause/resume + notification + stale-job system, and `add_credits`.

### Non-goals
- Changing credit *package* pricing (1/$10, 3/$24, 5/$35, 10/$60 stay as-is).
- Per-second or sub-tier pricing. Tiers are coarse (10-minute buckets) on purpose.
- Reworking the audio-edit feature itself.
- Retroactive re-pricing of historical jobs.

---

## 3. Pricing model

```
credits = max(1, ceil(duration_seconds / 600))      # 600s = 10 min per tier
blocked = duration_seconds > 3600                    # > 60 min
```

| Duration | Credits |
|---|---|
| 0:00 – 10:00 | 1 |
| 10:01 – 20:00 | 2 |
| 20:01 – 30:00 | 3 |
| 30:01 – 40:00 | 4 |
| 40:01 – 50:00 | 5 |
| 50:01 – 60:00 | 6 |
| 60:01+ | **blocked** |

Boundaries are inclusive at the tier top (exactly 60:00 → 6 credits, allowed; 60:01 → blocked).

The frontend renders cost for display from the same tier logic, but **the backend
`duration_to_credits()` util is the single source of truth** — every actual charge re-derives
credits server-side from a server-measured duration. A client-supplied credit number is only
ever used to detect a mismatch (the user saw a different price than we will charge → re-confirm),
never to determine the charge.

---

## 4. Architecture overview

The charge lands wherever duration first becomes **authoritative**, with a post-download (and
post-edit) reconciliation safety net for sources where the early number is only an estimate.

| Source | Pre-flight estimate | Authoritative measurement | Reconcile? |
|---|---|---|---|
| **Upload** | `ffprobe` on uploaded GCS file (exact) | at `uploads-complete` | only if a later audio-edit changes length |
| **YouTube / URL** | flacfetch `/check-youtube` metadata (no download) | post-download ffprobe | only if metadata disagrees (rare) |
| **Torrent search (RED/OPS)** | search-result `duration` metadata (may lie / be missing) | post-download ffprobe | yes — expected path |
| **Any source + audio edit** | (as above) | ffprobe of the *edited* audio, just before separation | yes — measured post-trim |

Two checkpoints, both reusing existing patterns:

1. **Pre-flight charge** — before any heavy work. Estimate → confirm modal (shows `Xm = N
   credits`, balance, inline Buy Credits if short) → deduct N credits server-side.
2. **Reconciliation** — at the single convergence point immediately before the
   separation+transcription stage is triggered. Re-measure the audio that will actually be
   processed (post-edit if edited); settle the difference.

---

## 5. Data model & job state

### New job status
`JobStatus.AWAITING_DURATION_CONFIRM` — inserted logically between `DOWNLOADING` and
`SEPARATING_STAGE1`. It is a **blocking** human-checkpoint state, like `AWAITING_REVIEW`.

It is entered for two distinct reasons, distinguished by `state_data.duration_confirm_reason`:
- `"preflight"` — upload flow, after the file lands and is measured, before charging/triggering.
- `"reconcile"` — post-download / post-edit, when the actual cost is **higher** than what the
  user already confirmed and paid.

### `state_data` fields (all under the job's `state_data`)
| Field | Type | Meaning |
|---|---|---|
| `duration_estimate_seconds` | float \| null | best pre-flight estimate |
| `duration_estimate_source` | str | `upload_ffprobe` \| `youtube_metadata` \| `search_metadata` \| `unknown` |
| `duration_actual_seconds` | float \| null | authoritative measurement (post-download / post-edit) |
| `credits_charged` | int | running total credits deducted for this job (the refund/cancel basis) |
| `duration_confirm_reason` | str \| null | `preflight` \| `reconcile` while in `AWAITING_DURATION_CONFIRM` |
| `pending_additional_credits` | int \| null | credits required to clear a `reconcile` pause |
| `duration_confirmed` | bool | true once the user has confirmed the authoritative cost |

`credits_charged` is **not** added to `SUMMARY_FIELD_PATHS` unless the dashboard needs it
(see the summary-projection gotcha); the confirm UI fetches the full job. If any of these
fields must appear in the job-list summary, add to **both** `SUMMARY_FIELD_PATHS`
(`firestore_service.py`) **and** `_SUMMARY_STATE_DATA_KEYS` (`jobs.py`), with a regression test.

---

## 6. Pricing util (new)

`backend/services/pricing.py`:

```python
import math

SECONDS_PER_CREDIT_TIER = 600          # 10 minutes
DURATION_CREDIT_BLOCK_SECONDS = 3600   # 60 minutes

def duration_to_credits(seconds: float) -> int:
    """Credits required to process `seconds` of audio. Minimum 1."""
    return max(1, math.ceil(seconds / SECONDS_PER_CREDIT_TIER))

def is_blocked(seconds: float) -> bool:
    return seconds > DURATION_CREDIT_BLOCK_SECONDS
```

Pure, dependency-free, exhaustively unit-tested. The frontend mirrors these two constants in a
small TS helper for display only (kept in sync via a comment cross-reference; the server stays
authoritative).

---

## 7. Pre-flight: estimate, confirm, charge (per source)

### 7.1 Audio search (RED / OPS / YouTube results)
Duration is already present in search results (`AudioSearchResultResponse.duration`,
`audio_search.py:166`). For the search flow the job is created (status `PENDING`) at search
time, *before* a result is chosen, so the **charge lands at `/select`** — that is the first
moment we know which result (and therefore which duration) the user picked. The guided flow
(`GuidedJobFlow.tsx`) shows per-result cost (`Xm · N credits`) and a confirm step; `/select`
carries `acknowledged_credits`, and the handler re-derives credits from the chosen result's
duration and **deducts N atomically**. No pre-processing pause. Torrent-sourced results display
the label *"estimated — final cost confirmed after download."* (The base search-create call
performs no duration charge; if the search itself should require ≥1 credit to start, keep the
existing has-credits gate without deducting.)

### 7.2 URL (YouTube etc.)
New endpoint `POST /api/jobs/estimate` accepts `{ url }`, runs the existing flacfetch
`/check-youtube` metadata probe (no download), returns `{ duration_seconds, credits, blocked }`.
Frontend shows the confirm modal; on confirm it creates the job with `acknowledged_credits`,
and `create_job` deducts the server-derived N. If the probe fails (e.g. yt-dlp bot challenge),
return `duration_seconds: null, source: "unknown"`; the frontend warns "final cost confirmed
after download", the job is created with a **1-credit hold**, and post-download reconciliation
settles it.

### 7.3 Upload
The existing two-phase signed-URL flow already lands the file in GCS before processing
(`create-with-upload-urls` → client uploads → `uploads-complete`). At `uploads-complete`, run
`_get_audio_duration_ffprobe_signed()` (header-only, exact). If `is_blocked` → reject + refund
nothing (nothing charged yet) + delete job. Otherwise transition to
`AWAITING_DURATION_CONFIRM(preflight)` **without charging or triggering workers**. The frontend
shows the confirm modal; `POST /api/jobs/{id}/confirm-duration` deducts N and triggers the
download/processing path.

### 7.4 Common pre-flight behaviour
- **> 60 min** at pre-flight → reject before any charge, with the "inputs over 60 minutes aren't
  supported" message (i18n key).
- **Insufficient credits** at the modal → inline **BuyCreditsDialog**; on purchase, return to
  the same confirm step and proceed (no lost context).
- **Admin jobs** (`is_admin=True`) bypass charging entirely, as today.

---

## 8. Reconciliation checkpoint

A shared async helper `measure_and_reconcile(job_id)` is called at the single convergence point
**immediately before** `asyncio.gather(trigger_audio_worker, trigger_lyrics_worker)` in BOTH
paths:

- No-edit path: `backend/workers/audio_download_worker.py:283`
- Post-edit path: `backend/api/routes/review.py:2192`

This guarantees we always measure **the audio that is about to be sent to separation +
transcription** — i.e. the post-edit (trimmed) audio when an edit occurred, which is exactly the
audio AudioShake bills for. Logic:

```
actual = ffprobe(job.input_media_gcs_path)        # the to-be-processed audio
store duration_actual_seconds = actual
if is_blocked(actual):                            # metadata lied; over the ceiling
    refund all credits_charged
    cancel job (FAILED) with over-limit message + email
    return  # do NOT trigger processing

required = duration_to_credits(actual)
delta = required - credits_charged
if delta == 0:
    proceed (trigger workers)
elif delta < 0:                                   # shorter than estimate
    add_credits(+abs(delta), reason="duration_refund")
    credits_charged = required
    proceed
else:                                             # longer than estimate
    pending_additional_credits = delta
    duration_confirm_reason = "reconcile"
    transition → AWAITING_DURATION_CONFIRM         # worker exits; do NOT trigger workers
```

Clearing a `reconcile` pause: the frontend shows "this turned out longer — N more credits"
(inline-buy if short); `POST /api/jobs/{id}/confirm-duration` deducts
`pending_additional_credits`, sets `duration_confirmed`, increments `credits_charged`, and
resumes by triggering the audio+lyrics workers — identical to the review-gate resume.

> Because measurement happens at the convergence point, edited jobs are never pre-charged on the
> pre-edit length and then forced to reconcile down later: the no-edit path reconciles at
> download, while edit jobs branch to `AWAITING_AUDIO_EDIT` *before* the gather and reconcile
> only once, post-edit.

---

## 9. Notifications & timeouts (mirror the review stage)

`AWAITING_DURATION_CONFIRM` reuses the review-stage pause/resume + notification + stale-job
infrastructure end-to-end.

### In-browser (app open)
- Add `AWAITING_DURATION_CONFIRM` to `STATUS_CONFIG` (`frontend/lib/job-status.ts`) as blocking
  (amber), and to `isNotifiableBlockingStatus()`.
- The existing `useJobNotifications` polling hook (`frontend/hooks/use-notifications.ts:166`)
  then auto-fires the alert chime + title flash on transition.
- The opt-in Web Push path (`push_notification_service.py`, triggered from
  `job_manager.py:687`) also fires, for users who navigated away.

### 15-minute email reminder
- Reuse the Cloud Tasks idle-reminder. It already accepts a configurable delay (review uses
  `IDLE_REMINDER_DELAY_SECONDS = 2*60`); schedule the duration-confirm reminder with a **15-minute
  (900s)** delay.
- Add an `action_type` case for duration-confirm in `_schedule_idle_reminder`
  (`job_manager.py:644`) and the blocking-state check in
  `internal.py` (`/check-idle-reminder`, ~line 393).
- New email template method `email_service.send_duration_confirm_reminder(...)` mirroring
  `send_review_reminder` (i18n subject/body, all locales).

### 24h reminder / 48h auto-cancel
- Extend the hourly `stale_review_processor` (`backend/workers/stale_review_processor.py`) to
  also scan `AWAITING_DURATION_CONFIRM` (add to the status list at ~line 49), reusing the
  existing `REVIEW_REMINDER_HOURS = 24` / `REVIEW_EXPIRY_HOURS = 48` thresholds.
- At 24h: second reminder email.
- At 48h: **auto-cancel and refund all `credits_charged`** (`add_credits(+credits_charged,
  reason="duration_confirm_expired")`, `cancel_job(...)`), and send the "expired" email
  (new `send_duration_confirm_expired` template).
- `blocking_state_entered_at` is already set on entering any blocking state — reused as the clock.

> Note: a `preflight` upload pause and a `reconcile` pause both use this same machinery. The
> 48h auto-cancel of a `preflight` pause simply deletes/cancels an un-charged or 1-credit-hold
> job and refunds whatever was charged.

---

## 10. API surface

| Method | Path | Purpose | Notes |
|---|---|---|---|
| `POST` | `/api/jobs/estimate` | URL duration probe → `{duration_seconds, credits, blocked, source}` | new; runs `/check-youtube` |
| `POST` | `/api/jobs/{id}/confirm-duration` | confirm cost, deduct credits, resume | new; serves both `preflight` and `reconcile` |
| `POST` | `/api/jobs` | URL create | + `acknowledged_credits`; charges `duration_to_credits(estimate)` |
| `POST` | `/api/audio-search/{id}/select` | search select | re-derive credits from result duration; charge |
| `POST` | `/api/jobs/{id}/uploads-complete` | finalize upload | ffprobe → route to `AWAITING_DURATION_CONFIRM(preflight)` instead of charging+triggering |
| `POST` | `/api/internal/process-stale-reviews` | hourly scan | now also scans `AWAITING_DURATION_CONFIRM` |
| `POST` | `/api/internal/jobs/{id}/check-idle-reminder` | idle email | now also handles duration-confirm action_type |

`confirm-duration` request: `{ acknowledged_credits: int }` (must match the server-computed
required credits, else 409 → frontend refreshes the figure). Response: updated job.

Credit deduction at create/confirm uses the **transactional** `deduct_credit` path generalised
to deduct N atomically (extend `deduct_credit` to take an `amount`, or add `deduct_credits`),
preserving the existing race-safe Firestore transaction and the job-deletion-on-failure rollback.
Refunds use `add_credits(+n, reason=...)`.

---

## 11. Frontend

- **`DurationCostConfirm` modal** (new) — formatted duration, credit cost, current balance,
  Confirm button or inline Buy Credits. Reused for: URL pre-flight, upload pre-flight pause,
  and reconcile pause. Driven by job `state_data` (`duration_estimate_seconds`,
  `duration_actual_seconds`, `pending_additional_credits`, `duration_confirm_reason`).
- **Search results** — per-result `Xm · N credits` chip; torrent results carry the "estimated"
  tooltip.
- **Guided flow** — cost confirm step before submit (search/URL); carries `acknowledged_credits`.
- **Dashboard banner** — on `AWAITING_DURATION_CONFIRM`, same component family as the
  awaiting-review banner, deep-linking to the confirm modal.
- **Over-60-min** — inline rejection message in the create flow.
- All strings go through `messages/en.json` + `t()` / `useTranslations()`; run
  `python scripts/translate.py --messages-dir ./messages --target all` so CI passes (33 locales).

---

## 12. i18n keys (indicative)

`pricing.creditsForDuration` ("{minutes} min · {credits} credits"),
`pricing.estimatedLabel`, `pricing.overLimit` (">60 min not supported"),
`pricing.confirmTitle`, `pricing.confirmBody`, `pricing.reconcileTitle`,
`pricing.reconcileBody` ("turned out longer — {credits} more credits"),
`pricing.insufficientCredits`, `email.durationConfirmReminder.*`,
`email.durationConfirmExpired.*`, plus a job-status label for `awaiting_duration_confirm`.

---

## 13. Edge cases

- **Estimate unknown** (probe failed / torrent metadata missing) → 1-credit hold, label as
  estimate, reconcile post-download (will almost always `reconcile`-pause if actually long).
- **Actual shorter than estimate** → auto-refund, proceed silently.
- **Actual over 60 min despite a passing estimate** → refund everything, cancel, email.
- **Audio edit lengthens vs shortens** → both handled; measurement is post-edit at the
  convergence point.
- **User buys credits mid-confirm** → BuyCreditsDialog returns to the same step; idempotent.
- **Race on deduction** → existing transactional deduct + job-deletion rollback preserved for N.
- **Admin / made-for-you** → bypass charging (unchanged).
- **Retry after a failed download** → `duration_actual_seconds` recomputed on the fresh file;
  reconciliation is idempotent against `credits_charged`.
- **48h expiry of a `preflight` (un-charged) upload** → cancel + delete; refund of 0 or the
  1-credit hold.

---

## 14. Testing

- **Unit** — `duration_to_credits` / `is_blocked` boundary table: 0, 599, 600, 601, 3000, 3001,
  3600, 3601. `measure_and_reconcile` delta logic (==, <, >, blocked) with mocked ffprobe.
- **Integration** — each source's estimate→confirm→charge path; reconcile up (pause→confirm→
  resume), reconcile down (auto-refund), over-limit (refund+cancel), insufficient-credits inline
  buy, probe-failure 1-credit-hold fallback, post-edit measurement.
- **Notifications** — idle-reminder scheduled at 15 min for the new action_type; stale processor
  picks up `AWAITING_DURATION_CONFIRM` at 24h/48h; expiry refunds `credits_charged`.
- **Regression** — short/normal songs still effectively cost 1 credit; admin bypass unaffected;
  summary projection unaffected (or covered if a field is added).

---

## 15. Rollout

- Ship as a normal release; no infra rebuild beyond a Cloud Scheduler/Cloud Tasks config that
  already exists (only the status list + action_type expand).
- Credit-package amounts unchanged; communicate the pricing-by-length change in release notes /
  UI copy.
- Feature is inherently always-on (it changes the charge path); consider a config flag
  `DURATION_PRICING_ENABLED` defaulting on, to fall back to flat-1 if a serious issue appears.

---

## 16. File-by-file change map

**Backend**
- `backend/services/pricing.py` — **new** util.
- `backend/services/user_service.py` — generalise `deduct_credit` to `amount` (or add
  `deduct_credits`); `add_credits` already supports refunds.
- `backend/services/job_manager.py` — charge N at create; `_schedule_idle_reminder`
  duration-confirm action_type; `measure_and_reconcile` orchestration helper.
- `backend/workers/audio_download_worker.py:283` — call `measure_and_reconcile` before gather.
- `backend/api/routes/review.py:2192` — call `measure_and_reconcile` before gather.
- `backend/api/routes/jobs.py` — `/estimate`, `/confirm-duration`; `acknowledged_credits` on
  create; reuse `_get_audio_duration_ffprobe_signed`.
- `backend/api/routes/audio_search.py` — charge N on `/select` from result duration.
- `backend/api/routes/file_upload.py` — `uploads-complete` routes to
  `AWAITING_DURATION_CONFIRM(preflight)`.
- `backend/api/routes/internal.py` — idle-reminder + stale-review extended to new state.
- `backend/workers/stale_review_processor.py` — scan new state; expiry refund.
- `backend/services/email_service.py` — `send_duration_confirm_reminder`, `_expired`.
- `backend/models/job.py` — `AWAITING_DURATION_CONFIRM` status.

**Frontend**
- `frontend/lib/job-status.ts` — `STATUS_CONFIG` + `isNotifiableBlockingStatus`.
- `frontend/lib/pricing.ts` — **new** display helper (mirrors backend constants).
- `frontend/components/job/DurationCostConfirm.tsx` — **new** modal.
- `frontend/components/job/GuidedJobFlow.tsx` — cost confirm step; `acknowledged_credits`.
- `frontend/components/audio-search/AudioSearchDialog.tsx` — per-result cost chips.
- `frontend/lib/api.ts` — `estimate`, `confirmDuration` clients.
- dashboard banner component — handle new state.
- `frontend/messages/en.json` (+ `translate.py --target all`).

**Infra** — only config-list expansion (no new resources expected); confirm the idle-reminder
Cloud Task delay is per-call.

---

## 17. Open questions

None blocking. Optional follow-ups: (a) surface estimated cost in the audio-search result list
*before* the user opens the confirm step (already covered by the per-result chip); (b) whether
`DURATION_PRICING_ENABLED` flag is worth the maintenance — recommended for the first release,
removable later.
