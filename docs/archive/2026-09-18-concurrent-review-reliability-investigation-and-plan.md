# Concurrent-load reliability: investigation findings + systematic plan — 2026-09-18

Spec: `docs/archive/2026-09-17-concurrent-review-reliability-verbatim-record.md` (Andrew's
verbatim prompt — the four problem statements below map 1:1 to it).

## Reproduction (prod, 2026-09-18 ~03:35Z) — CONFIRMED

Method: `frontend/test-concurrency.local.sh` (gitignored) — 10 simulated review tabs, each
firing the page's real GET trio (`correction-data`, `waveform-data`, `audio/vocals`) with
per-job `review_token`s pulled from Firestore via ADC.

**Baseline (sequential, one job):** ping 0.31s · correction-data 0.83s · waveform-data 1.35s ·
instrumental-urls 0.47s · instrumental-analysis **13.7s** · audio/vocals **27.7s** (full 4.3MB
body; Range header ignored — 200 not 206).

**Burst (10 tabs in parallel):**
- 8 of 30 requests → **HTTP 500** (7× audio/vocals, 2× correction-data — one job had both)
- surviving requests took **44–72s** (correction-data up to 70.6s vs 0.83s baseline)
- Cloud Run logged **9× "The request was aborted because there was no available instance"**
  at 03:35:32Z — the instance was so wedged that Cloud Run couldn't dispatch to it, despite
  only ~30 in-flight requests vs `--concurrency 80`.
- Frontend consequences (from code, `frontend/lib/api.ts`): GETs have a 45s hard timeout + 3
  attempts, then throw `BackendUnavailableError` → **"The server is temporarily unavailable.
  Please try again in a moment."** under "Failed to load lyrics". Tracked GETs stalled ≥10s /
  ≥20s + a failed 4s `/api/health` probe → the orange **"We're having trouble reaching our
  servers"** banner. Both exactly match Andrew's 1–2-tabs-load-8-fail observation.

**Ruled out:** Cloudflare edge rate limiting (50 req/10s/IP is live in prod — `edge:enabled`,
`proxyProdApi=true` — but 60 parallel cheap pings all returned 200 fast; the rule never
tripped, and cheap-endpoint concurrency is fine). The meltdown is entirely origin-side and
entirely caused by the *heavy* endpoints.

## Root causes (evidence-backed)

**RC1 — `/waveform-data` does CPU-bound audio decoding ON the event loop, uncached.**
`backend/api/routes/review.py:2107` calls `AudioAnalysisService().get_waveform_data()`
synchronously in the async handler: downloads the **raw backing-vocals FLAC** (tens of MB) from
GCS, decodes it fully via pydub→ffmpeg, then runs a 1000-iteration pure-Python RMS loop
(`karaoke_gen/instrumental_review/waveform.py:163-210`). A GCS waveform cache EXISTS
(`audio_analysis_service.py:246-315`) but is only wired to the audio-editor `input-audio-info`
path — NOT to `/waveform-data`. Every review load recomputes from scratch, and while it runs,
**every other request on that instance is frozen** (single uvicorn process, single event loop,
no `--workers` — `backend/Dockerfile:50`).

**RC2 — blocking Firestore/GCS calls on the event loop in every review endpoint.**
`get_job()` (sync Firestore read) is called directly in async handlers; `correction-data` also
does sync `file_exists` + `download_json` (large corrections JSON) + a Firestore write
(`transition_to_state`). Under contention these serialize behind RC1.

**RC3 — multi-MB audio byte-serving through the Python proxy, slow and uncached.**
`/audio/vocals` streams 3–8MB per tab at ~700KB/s observed (5–28s each), re-downloading from
GCS every request (the LRU byte cache `_review_audio_cache` cap 16 covers only
`/instrumental-audio/{option}`). No Range support (ignores the header, returns 200 full-body).
Ten tabs = 30–80MB pushed through one 2-vCPU throttled container.

**RC4 — Cloud Run shape concentrates the burst on one instance.**
`--concurrency 80 --min-instances 1 --max-instances 20 --cpu 2 --cpu-throttling` (ci.yml
~:1979-1998; service is deployed via gcloud in CI, NOT Pulumi). 10 tabs ≈ 30–50 requests ≪ 80,
so the autoscaler sees no reason to scale out; everything lands on the one warm instance until
it's wedged enough to abort dispatches ("no available instance").

**RC5 — Waveforms-mode strips: client decodes the whole vocals stem, with silent give-up.**
The per-segment strips do NOT use `/waveform-data`; `VocalsAudioDataLoader.tsx` raw-fetches the
full `/audio/vocals` OGG, decodes with WebAudio, computes peaks client-side
(`lib/audio-data.ts`, 400 peaks/s). On HTTP 202 (stem not ready) it polls every **15s up to 40
tries**; on ANY other error (e.g. the burst 500s, or a timeout) it **gives up silently** —
strips never appear until a manual reload. No loading indicator exists (TimelineEditor renders
blank space). First-paint delay = slow 3–8MB proxy download + decode, or a swallowed error →
matches "wait 1+ minutes" (Image #3/#4).

**RC6 — the orange banner fires "correctly" but the origin is too easy to stall, and there's
ZERO telemetry.** Banner logic (`lib/backend-status.ts`): oldest tracked in-flight GET ≥10s →
reconnecting, ≥20s → unavailable, gated on a 4s `/api/health` probe failing. During preview
encoding (Image #2) the GCE-worker-with-LOCAL-FALLBACK path can run ffmpeg on the API instance
itself; on a 2-vCPU CPU-throttled container that stalls the status polls AND the health probe →
banner, with no real outage. Nothing is logged anywhere when the banner or the
"temporarily unavailable" error is shown (confirmed: no gtag events, crash-reporter never
invoked for caught errors) — Andrew has no visibility into user-experienced frequency.

## Plan (phased; each phase independently shippable)

### Phase 1 — stop the bleeding server-side (highest leverage, code-only) — ✅ IMPLEMENTED (this branch, v0.230.0), plus the Phase-4 waveform silent-give-up retry fix
1. `/waveform-data`: wire the EXISTING GCS waveform cache; compute via `asyncio.to_thread`;
   precompute in `screens_worker` alongside `prepare_review_audio_for_job` (OGG pre-warm
   already happens there); decode from the small transcoded OGG, not the raw FLAC.
2. Wrap blocking Firestore/GCS calls in review-path handlers with `to_thread` (helper:
   `run_sync()`), starting with `get_job`, `download_json`, `file_exists`,
   `transition_to_state`.
3. Audio byte-serving: honor Range (206) so browsers/audio elements fetch incrementally;
   extend the LRU byte cache to vocals + input audio; add `Cache-Control: private, immutable,
   max-age=…` so repeat loads hit browser cache.
4. Admission control for CPU-heavy review endpoints (small semaphore + fast 503-with-Retry-After
   rather than wedging the loop) — mirrors the v0.228.1 signing-pool pattern.

### Phase 2 — Cloud Run shape (deploy-config)
5. Lower `--concurrency` for karaoke-backend (e.g. 12–16) so bursts trigger scale-out; consider
   `--no-cpu-throttling`; consider `--workers 2` (check in-memory cache/locks assumptions);
   evaluate uvloop. Revisit preview-video LOCAL fallback running ffmpeg on the API instance —
   route to the encoding pool or cap it.

### Phase 3 — degradation telemetry (Andrew's explicit ask)
6. `POST /api/client-events` (batched, lightweight, auth-optional): frontend reports
   `banner_reconnecting`, `banner_unavailable`, `lyrics_load_failed`, `waveform_slow` (>Xs),
   with metadata (job_id, user/session, page, oldest-stall age, probe result, http status,
   locale, UA). Backend emits structured Cloud Logging events (`jsonPayload.event=...`) —
   persistent, queryable via Log Analytics, zero new infra; optional log-based metric + alert.

### Phase 4 — banner tuning + Waveforms UX (frontend)
7. Banner: require 2 consecutive probe failures (or longer probe timeout) before `unavailable`;
   exempt/raise thresholds for known-slow byte GETs; show `reconnecting` pill only after a
   probe failure too.
8. Waveforms mode: loading indicator on the strip area; retry-with-backoff on transient errors
   instead of silent give-up; consider serving a precomputed vocals peak JSON (from Phase 1's
   cached waveform work) so strips paint instantly without a client-side full decode.

### Phase 5 — regression guard
9. Formalize `test-concurrency.local.sh` into a repeatable load-test script (checked in) + a
   Playwright multi-tab prod E2E; record before/after numbers in this doc.

## Open questions for Andrew
- OK to lower Cloud Run concurrency (cost: more instances under load)? min-instances stays 1.
- Telemetry sink: structured Cloud Logging (proposed, free-ish) vs Firestore collection?

## Verification numbers to beat (from this session's repro)
- Burst 10-tab: 0 failed requests (was 8/30); worst correction-data < 5s (was 70.6s);
  no "no available instance" aborts; waveform strips present on first paint for a warm job.
