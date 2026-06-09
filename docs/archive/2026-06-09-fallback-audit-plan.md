# Fallback Logic Audit — karaoke-gen (2026-06-09)

**Driver prompt:** `docs/archive/2026-06-08-fallback-logic-audit-prompt.md`
**Motivating incident:** 2026-06-08 Facebook-URL job → generic "Failed to download
audio file". A non-YouTube path silently fell back to running yt-dlp *inside the
Cloud Run container*, which died in a broken `convert_to_wav`. flacfetch was
supposed to be the sole downloader. The fix **deleted** the fallback rather than
patching it. See `docs/archive/2026-06-08-flacfetch-sole-downloader-plan.md` and
memory `project_flacfetch_sole_downloader`.

**Goal of this audit:** make every silent fallback an *explicit, intentional*
decision — fail loudly with a clear error, OR keep + log/alert, OR something
else. Do **not** change behaviour without Andrew's answer. Some fallbacks are
load-bearing graceful degradation and should stay.

**Classification:**
- **A** = legitimate graceful degradation (intended, fails safe + visible). Keep.
- **B** = silent fallback masking a bug/misconfig (like the yt-dlp case). Delete or fail-fast.
- **C** = ambiguous — needs Andrew's intent.

**Method:** 5 parallel read-only triage agents over (1) audio/lyrics acquisition,
(2) job-state & worker triggering, (3) payment/credits/referral/auth, (4)
karaoke_gen core render, (5) backend routes/services/config. Findings below are
verified against actual behaviour, not comments.

> **DECISION fields are blank until Andrew answers.** Capture his words verbatim.

---

## THEME 1 — Source downgrade: flacfetch → YouTube-only (the motivating class)

These reconstruct the exact "silently use a worse source" pattern that caused the
incident. flacfetch is supposed to be the sole/lossless downloader.

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 1.1 | `backend/services/audio_search_service.py:224-230` | **B** | Remote flacfetch search error → silently re-runs **local** flacfetch (YouTube-only on Cloud Run). Transient remote outage silently downgrades a RED/OPS lossless job to a YouTube rip; user sees only a `logger.warning`. |
| 1.2 | `karaoke_gen/audio_fetcher.py:405-433` | **B** | If `TorrentDownloader` import fails / Transmission down / tracker `*_API_URL` unset → RED/OPS skipped, only `YoutubeProvider` registered. The concrete mechanism behind 1.1. |
| 1.3 | `karaoke_gen/audio_fetcher.py:1818-1823` | **C** | If exactly one of `FLACFETCH_API_URL` / `FLACFETCH_API_KEY` is set → logs "falling back to local mode". A typo'd secret silently disables the remote service. |
| 1.4 | `karaoke_gen/audio_fetcher.py:558-577` | **C** | `select_best` returns result[0] on any ranking failure → may silently auto-pick a lower-quality source. |

**DECISION (Theme 1):** Andrew — **"Fail loudly, no YouTube fallback."** flacfetch
is the sole/lossless downloader; if it can't serve (remote error, RED/OPS
unavailable, half-configured), fail the job with a clear error rather than
silently ripping YouTube. Applies to 1.1–1.4.

---

## THEME 2 — Silent worker-trigger drops → jobs stall forever

A recurring pattern: a downstream worker is triggered fire-and-forget and the
boolean/return is discarded. If the enqueue fails, the job sits in an intermediate
state with **no error and nothing to recover it**. The user is often told "success".

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 2.1 | `backend/services/job_manager.py:1126-1137` | **B** | `_trigger_screens_worker` catches `RuntimeError` (no event loop) and just logs `# TODO: use message queue`. Called from `mark_lyrics_complete` in the **lyrics_worker Cloud Run Job** (sync). If it no-ops, `lyrics_complete=True` is written but screens worker never fires → job stalls before AWAITING_REVIEW; `recover_stuck_jobs` doesn't cover this. *(Verify: does a loop exist in that Job?)* |
| 2.2 | `backend/api/routes/review.py:504` | **B** | After REVIEW_COMPLETE, `asyncio.create_task(trigger_render_video_worker)` result discarded; endpoint returns `{"status":"success"}`. Failed enqueue → stuck at REVIEW_COMPLETE, no error, no auto-recovery. |
| 2.3 | `backend/workers/render_video_worker.py:281,546` | **B** | `await trigger_video_worker(job_id)` bool ignored. Failed trigger → job stuck at INSTRUMENTAL_SELECTED with render done but final encode never started. |
| 2.4 | `backend/api/routes/internal.py:725-728` | **C** | Retry scheduler resets parked job to REVIEW_COMPLETE then triggers; on raise the comment assumes it'll be re-picked as RENDER_PENDING_CAPACITY, but the next tick queries `status==RENDER_PENDING_CAPACITY` and won't see it. Escapes both retry query and 24h timeout sweep → stuck indefinitely. |
| 2.5 | `backend/services/encoding_service.py:1018-1020` | **B** | `get_encoding_service()` does `try: set_worker_manager(manager) except Exception: pass`. Any client-init failure → service silently runs with **no worker manager**, falls back to static URL, loses multi-zone capacity failover, no signal. |
| 2.6 | `audio_download_worker.py:291-294`, `job_manager.py:1078-1081`, `jobs.py:70-71`, `review.py:2208-2209` | **B** | `asyncio.gather(trigger_audio, trigger_lyrics)` results discarded across these sites. One failed enqueue → that pipeline branch silently never starts while the other proceeds; job hangs. |

**Model to emulate:** `backend/services/visibility_change_service.py:194-205` does it
right — checks `triggered`, logs error, restores state, raises.

**DECISION (Theme 2):** Andrew — **"Fail loud / re-park & retry."** Check every
trigger result; on failure mark the job failed or re-queue (the
`visibility_change_service` pattern) so a job can never silently stall. Applies
to 2.1–2.6. _(Still verify reachability of 2.1's no-event-loop path during impl,
but the directional call is fail-loud/retry.)_

---

## THEME 3 — Lyrics/transcription silently degraded or missing

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 3.1 | `karaoke_gen/lyrics_transcriber/core/controller.py:487-491` | **B** | If every transcriber returns empty (no exception, e.g. AudioShake 200 with no words), code logs a warning and proceeds; `process()` skips output but job is still reported "completed successfully". → karaoke video with **no synced lyrics**, no error. |
| 3.2 | `karaoke_gen/lyrics_processor.py:372-373` + `core/config.py:16` | **B/C** | `enable_local_whisper=True` hardcoded as priority-3 fallback. On a host with `whisper_timestamped` installed, AudioShake failure silently yields much lower-quality Whisper timing with no signal. (No-op on Cloud Run where pkg absent — env-dependent.) |
| 3.3 | `core/controller.py:396-398` | **C** | Failure loading existing corrections JSON is swallowed → silently re-transcribes from scratch, discarding prior **human review** work. |
| 3.4 | `core/controller.py:442-454` | **C** | Per-provider reference-lyric fetch errors (Genius/Spotify/LRCLIB/Musixmatch) swallowed; all-fail → "No lyrics found", correction runs transcription-only (lower accuracy). Masks systemic outages (e.g. expired Spotify cookie). |

**DECISION (Theme 3):** Andrew — **"Fail the job loudly."** No transcription
results = fail with a clear error; lyrics are the core deliverable and a
lyric-less "success" must never ship. Treat 3.1 as a hard fail. _(Apply the same
fail-loud principle to 3.2–3.4 during impl — silent low-quality Whisper and
silently discarding human-review corrections both violate the principle; flag if
any needs a different call.)_

---

## THEME 4 — Wrong font / asset / color on a paid deliverable

Silent substitution of a missing branded asset → a wrong-looking video/CDG the
customer paid for. Note the karaoke-render path at
`lyrics_transcriber/output/video.py:43-44` already **raises** on a missing
background — a good loud-failure model the others diverge from.

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 4.1 | `lyrics_transcriber/output/video.py:417-423` | **B** | Final karaoke MP4: if theme font file missing, `:fontsdir` is silently dropped → libass resolves via system fontconfig → whole song in a substitute font, no log above debug. |
| 4.2 | `video_generator.py:120-132,153,174,191` | **B** | Title/end screens: missing font → `ImageFont.load_default()` (~10px bitmap). 4K title/end card renders artist/title in a near-invisible default font. |
| 4.3 | `video_generator.py:368-377` | **B/C** | Title/end screen: missing `background_image` → silently uses flat `background_color` (karaoke path raises instead — inconsistent). |
| 4.4 | `lyrics_transcriber/output/cdg.py:294-306` | **B/C** | CDG: unresolved theme font → bundled `arial.ttf` (warning only). Hardware disc renders all lyrics in Arial. |
| 4.5 | `karaoke_finalise.py:1132-1159` | **B/C** | Duet job: missing/malformed `duet_corrections_json` → renders CDG via LRC path = **solo colors**, loses singer-color distinction (warning only). |
| 4.6 | `lyrics_transcriber/output/cdg.py:551-561` | **B/C** | Duet CDG: lost/length-mismatched line→singer map → defaults every line to singer 1 "to keep the file valid". Silently wrong color attribution. |
| 4.7 | `lyrics_transcriber/output/ass/config.py:115-117,149-151` | **B/C** | Malformed theme color → hardcoded default blue/black. A typo'd theme color renders lyrics in the wrong brand color. |
| 4.8 | `lyrics_transcriber/output/ass/lyrics_line.py:39-45` | **B/C** | Text-measurement font → `load_default()` (different metrics) while libass renders a different font → line-wrap decisions desync from the actual video. Pairs with 4.1. |

**A (keep, verified intended + logged):** GPU→CPU encode fallback
(`karaoke_finalise.py:1803-1881`, visually equivalent libx264, warned);
`style_loader.py:164-192` documented default duet palette;
`subtitles.py:100-107` audio-duration estimate (logged).

**DECISION (Theme 4):** Andrew — **"Fail loudly on missing branded asset."** Match
`video.py:43-44` which already raises. A paid deliverable shouldn't silently
substitute fonts/backgrounds/colors — fail with a clear error so it's caught
before delivery. Applies to 4.1–4.8. _(Keep the confirmed-A items: GPU→CPU
encode, documented duet palette, audio-duration estimate.)_

---

## THEME 5 — Pricing undercharge / ceiling bypass

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 5.1 | `backend/api/routes/jobs.py:128-134` + `services/pricing.py:13-22` | **B** | Client-supplied `duration_seconds`; if `None`, `duration_to_credits(None)=1` and `is_blocked(None)=False`. Omitting duration → any-length job for 1 credit **and** skips the 60-min hard ceiling. `acknowledged_credits` guard only fires if client *sends* a mismatch. |
| 5.2 | `backend/api/routes/jobs.py:2446-2466` (`create_job_from_search`) | **B** | Guided/search flow never passes `credits=` → always `JobCreate.credits=1` regardless of length. Inconsistent with URL path; long-input undercharge. |
| 5.3 | `backend/api/routes/users.py:97` | **B** | Referral discount: `except Exception: discount_percent = 10` on Firestore read failure. A 20% link charged at 10% on a transient error. |

**DECISION (Theme 5):** Andrew — **"Keep defaults but always enforce ceiling +
alert."** Keep the 1-credit default, but the 60-min hard ceiling must always be
enforced (never silently bypassed when duration is omitted), and any
undercharge / discount-guess must raise a Discord alert so it's visible.
_Impl note:_ when duration is unknown we still alert + apply the ceiling rather
than reject; charge-by-duration consistency between URL and search flows to be
handled under this (alert if they diverge). Applies to 5.1–5.3.

---

## THEME 6 — Lost paid orders / "Unknown" customer data

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 6.1 | `backend/api/routes/users.py:1105-1135` (`_handle_made_for_you_order`) | **B** | Broad `except Exception` swallows fulfilment failure *after Stripe charged*; emails admin "[FAILED]" but does **not** re-raise → webhook returns 200 (no Stripe retry). If the email also fails (line 1134 logs only), a paid $50 order silently vanishes. |
| 6.2 | `backend/api/routes/users.py:858-859` | **B** | Made-for-you order: `metadata.get("artist","Unknown Artist")`/`("title","Unknown Title")` → a paid video titled "Unknown Artist - Unknown Title" instead of rejecting the order. |

**DECISION (Theme 6):** Andrew — **"Re-raise + durable failed-order record."** On
made-for-you fulfilment failure, re-raise so Stripe retries AND/OR write a
durable `needs_manual_fulfilment` record (don't rely on an email an admin might
miss); reject orders missing artist/title rather than shipping "Unknown Artist -
Unknown Title". Applies to 6.1–6.2.

---

## THEME 7 — Env-var / config defaults masking prod misconfiguration

A single unset/rotated env var silently produces wrong behaviour with only a
warning. Env vars are set in Cloud Run today, but the defaults are landmines.

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 7.1 | `backend/services/dropbox_service.py:34` | **B** | `GOOGLE_CLOUD_PROJECT` default `"karaoke-gen"` (real project is `nomadkaraoke`). If unset → secret lookup targets a non-existent project; bare `except` swallows → Dropbox distribution silently disabled. |
| 7.2 | `backend/config.py:19-21` | **B** | GCS bucket defaults `karaoke-gen-storage[/-temp/-outputs]` ≠ real `karaoke-gen-storage-nomadkaraoke`. If `GCS_BUCKET_NAME` unset → reads/writes a non-existent bucket, runtime 500s deep in processing instead of boot failure. |
| 7.3 | `backend/services/worker_service.py:160-166` | **B** | If `CLOUD_RUN_SERVICE_URL` unset → worker base URL = `http://localhost:{PORT}` with no env guard. Workers invoked against localhost → jobs created but silently never run. |
| 7.4 | `backend/middleware/tenant.py:30-31,102-105` | **B (security)** | `IS_PRODUCTION` from `ENV/ENVIRONMENT=="production"`, but app default is `"development"`. If prod doesn't explicitly set it → `?tenant=<id>` query override active → **tenant spoofing** (impersonate white-label portal). *(Verify prod sets ENVIRONMENT=production.)* |
| 7.5 | `backend/services/email_service.py:230-245` | **B** | No `POSTMARK_SERVER_TOKEN` → silent `ConsoleEmailProvider`; all sends "succeed". In prod with token unset/rotated, magic-link/completion/receipt emails silently vanish → users can't log in or get downloads. |

**DECISION (Theme 7):** Andrew — **"Fail-fast at startup in prod."** When
`ENVIRONMENT=production`, require these vars and refuse to boot (or go
health-check red) if missing; make the tenant-detection gate production-safe by
default (disable `?tenant=` override unless explicitly development). No silent
wrong-resource defaults. Applies to 7.1–7.5. _Impl note:_ first confirm prod
actually sets `ENVIRONMENT=production` (Pulumi `cloud_run.py`) — the security
gate 7.4 depends on it.

---

## THEME 8 — Lower-risk cleanups (docs lie, opaque errors, auth layers)

| # | file:line | Class | What happens when it triggers |
|---|-----------|-------|-------------------------------|
| 8.1 | `backend/config.py:77-78` & `credit_evaluation_service.py` module/`grant_welcome_credits` docstrings | **C/doc** | Docstrings say "Fail-open: credits granted on error" but code is **fail-CLOSED** (`pending_review`, no grant) — the *safe* behaviour. The lie risks a future maintainer "fixing code to match docs" and re-introducing free-credit abuse. Fix the docs, confirm policy. |
| 8.2 | `youtube_download_service.py:11,37-38`; `lyrics_worker.py:566` | **doc** | Stale docstrings still describe the deleted yt-dlp fallback ("falls back to local yt_dlp"). Misleading post-incident. |
| 8.3 | `backend/workers/audio_worker.py:467-473`, `lyrics_worker.py:611-623` → `audio_worker.py:217-218` | **C** | Distinct download failure causes all collapse to `return None` → generic user-facing "Failed to download audio file" (the opaque message from the incident). Surface specific reasons to the user? |
| 8.4 | `backend/api/routes/users.py:734-762` (`create_checkout`) | **C** | Unauthenticated; email + referral-discount taken from request body. Anyone can compute a discount against an arbitrary email. Direct money risk low (webhook keys off session metadata). Intended pre-login flow? |
| 8.5 | `backend/services/auth_service.py:107-111` | **C** | OIDC verified with `audience=None` (audience check skipped); mitigated by strict `token_email == scheduler SA`. Acceptable or tighten to URL allowlist? |
| 8.6 | `backend/services/auth_service.py:81-90` | **C** | Empty `ADMIN_TOKENS` → warning only; all admin endpoints 403 (fails closed = safe, but looks like an auth bug not a config bug). Louder signal in prod? |
| 8.7 | `backend/services/email_validation_service.py:226` | **C** | Missing Firestore blocklist doc → `DEFAULT_DISPOSABLE_DOMAINS` (the Firestore `.get` itself is unwrapped, so hard failures propagate — good). Acceptable degradation? |

**DECISION (Theme 8):** Andrew — **"Fix docs + specific errors + admin-token
signal."** Correct the lying/stale docstrings (8.1 fail-open→fail-closed, 8.2
yt-dlp), surface specific download-failure reasons to the user instead of the
generic "Failed to download audio file" (8.3), and add a loud prod signal when
`ADMIN_TOKENS` is empty (8.6). Leave 8.4 (unauth checkout), 8.5 (OIDC
audience=None), 8.7 (blocklist default) as acceptable fail-safe.

---

## A — Legitimate graceful degradation confirmed (no change; listed for the record)

- `audio_transcoding_service.py:113-130,171-175` — review-audio transcode fail → serve original FLAC (logged).
- `backend/workers/audio_worker.py:207-211` — audio separation not configured → **raises** (good model).
- `youtube_download_service.py:118-126` — flacfetch unconfigured → **raises** (the post-incident fix).
- `audio_download_worker.py:341-342` — unsupported source → raises `DownloadError` (post-fix state of the incident).
- `video_worker.py:182-185` — `_encode_via_gce` re-raises on GCE failure (no silent local fallback).
- `local_encoding_service.py:241-264` — GPU→CPU encode, warned. Visible perf degradation.
- `stripe_service.py` (multiple) — unconfigured/invalid → fail closed + visible.
- `user_service.py:1003-1017` — `add_credits` idempotent via `PROCESSED_STRIPE_SESSIONS`.
- `job_manager.py:146-164` — credit-deduction failure deletes job + raises `InsufficientCreditsError`.
- `referral_service.py:283-305` — `record_earning` returns None only for invalid/expired referral; wrapped so it never blocks the buyer's credits.
- `catalog_proxy_service.py:71,103` — autocomplete `[]` on karaoke-decide outage (cosmetic).
- `credential_manager` distribution checks — distinct NOT_CONFIGURED/INVALID/ERROR/VALID, blocking 400 on create-from-url.
- `file_upload.py:1827-1833` (create-from-url) — re-raises, calls `fail_job`, 500 with real detail.

---

## Execution roadmap (Phase 4) — one PR per theme, with tests

Ordered safest/clearest → most delicate. Each PR updates `docs/LESSONS-LEARNED.md`
with the principle ("don't invent silent fallbacks — ask or fail loud") + the
notable example(s) it fixes.

| PR | Theme | Decision in one line | Risk |
|----|-------|----------------------|------|
| 1 | **T8** docs/errors | Fix lying fail-open docstrings + stale yt-dlp docstrings; surface specific download-failure reasons; loud prod signal on empty ADMIN_TOKENS | Low |
| 2 | **T1** sourcing | flacfetch sole downloader → fail loud (no silent YouTube downgrade) across 1.1–1.4 | Low/Med |
| 3 | **T4** render assets | Missing branded font/bg/duet-color → raise (match video.py:43-44) across 4.1–4.8 | Med |
| 4 | **T7** env config | Prod fail-fast on missing required vars; tenant gate production-safe by default | Med (verify Pulumi env first) |
| 5 | **T3** lyrics | Empty transcription = fail loud; same principle to whisper/corrections/reference | Med |
| 6 | **T2** worker triggers | Check every trigger result → fail/re-park; never silently stall (2.1–2.6) | High (job state machine) |
| 7 | **T5** pricing | Keep 1-credit default but always enforce 60-min ceiling + alert on undercharge/discount-guess | High (money) |
| 8 | **T6** paid orders | Re-raise + durable needs_manual_fulfilment record; reject missing artist/title | High (money/webhook) |

**Status:** Phases 1–3 complete (discover + triage + interview; all 8 decisions
captured above). Phase 4 (execute) — pending sequencing go-ahead from Andrew.
