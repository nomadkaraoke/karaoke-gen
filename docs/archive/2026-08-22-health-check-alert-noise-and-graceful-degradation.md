# Health-check alert noise + frontend graceful degradation (2026-08-22)

## Trigger

A "Karaoke Backend - Service Unavailable" uptime alert fired at 06:06 UTC on
2026-08-22 and auto-recovered ~1m41s later. Investigation of the noise, plus a
follow-on request to make the frontend degrade gracefully during such blips.

## Root cause of the alert

**A routine, platform-initiated Cloud Run instance recycle — not a crash.**

- The backend runs on `--min-instances 1` (a deliberate cost choice).
- At 06:01:53 UTC Cloud Run reaped the single warm instance (which had run ~9.6h
  since the previous evening's deploy) and started a replacement
  (`Reason: MANUAL_OR_CUSTOMER_MIN_INSTANCE`, then `AUTOSCALING`). The new
  container's STARTUP probe passed ~06:03:13.
- During the ~80s gap (06:02:30–06:03:50) there was **no warm origin**, so
  Cloudflare failed the health check from **all 6** uptime checker regions
  simultaneously. Zero `/api/health` requests reached the origin in that window;
  no `SIGTERM`/OOM/error was logged (platform reaps leave no teardown log).
- The uptime alert (reworked 2026-08-19 to page only when >1 checker location
  fails) did its job — it caught a genuine, if brief, hard-down.
- A second, related failure mode was also observed (00:11–00:12 UTC): the single
  instance's event loop stalled (orchestrating an encoding-worker fallback
  cold-start with blocking timeouts), so health probes queued past the 10s
  uptime timeout across regions, then flushed at once.

Blast radius of such a blip: ~80s of API unavailability. The **frontend still
loads** (static Cloudflare Pages export), **Cloud Run Jobs** (video renders) and
**GCE encoding-worker** executions already in flight **keep running**; only
backend-side orchestration is briefly paused and self-heals via Cloud
Tasks/Scheduler retries + the recover-stuck-jobs / retry-pending-render crons.

## Change 1 — soften the alert (server-side)

`infrastructure/modules/monitoring.py`: the "Service Unavailable" condition
`duration` 60s → **300s**. A sub-5-min self-healing recycle/stall no longer
pages; a sustained hard-down still fires (~5–6 min). The read-retry budget in the
frontend (`lib/backend-status.ts`) is aligned to the same ~10s escalation.

We deliberately did **not** raise `--min-instances` to 2 (cost choice). If real
outages ever slip through undetected, that is the cheaper-detection lever and it
removes both failure modes at the source.

## Change 2 — graceful degradation (frontend)

Goal: during a blip, retry transparently and show one reassuring app-wide message
instead of every screen throwing a generic error.

- **`lib/backend-status.ts`** — framework-agnostic connectivity store
  (`online | reconnecting | unavailable`) + `useBackendStatus()`. **Status is
  derived purely from how long the OLDEST in-flight tracked read has been
  outstanding** (`beginRequest`/`endRequest`): `reconnecting` at
  `STALL_RECONNECTING_MS` (10s), `unavailable` at `STALL_UNAVAILABLE_MS` (20s),
  back to `online` the instant the stalled read settles.

  **Addendum (2026-08-23, v0.198.3): stall + health-probe confirmation.** A stall
  alone turned out not to be proof of an outage either — first-time
  instrumental-analysis / lyrics-review loads transcode audio server-side and can
  legitimately run 20–60s, false-firing the banner. Now, once a stall crosses the
  threshold, the store confirms with a cheap `GET /api/health` probe (registered by
  `lib/api.ts` via `configureHealthProbe`; 4s timeout, `Promise.race`d so a hung
  origin can't wedge it). While the probe answers OK — or has no verdict yet — the
  banner stays hidden; only a failed/timed-out probe (as during a real recycle,
  when the origin hangs) lets it surface (~4s later than before). The probe
  re-fires every `PROBE_FRESH_MS` (10s) while the stall persists, so recovery
  clears the banner even if the original read is still hung.
- **`lib/api.ts` — `apiFetch`** wraps every backend call (drop-in for `fetch`;
  internals use `globalThis.fetch`). Design decisions:
  - **The banner keys on a genuine STALL, not slowness.** A read that completes —
    even a normal-slow 3–5s one — surfaces nothing; only a read still outstanding
    past 10s shows the hint. (The original 2.5s "slow-request" hint was too
    trigger-happy — it fired during normal lyrics-review loads.)
  - **Only GETs are tracked, and only GETs get a hard-timeout (45s) backstop.**
    Long-running POSTs (preview video, generate, search, auto-correct) are
    legitimately slow: they must never trip the banner nor be aborted mid-encode.
    Callers can opt a slow read out with `opts.trackConnectivity: false`, or
    time-box any request with `opts.timeoutMs`.
  - **Retries GET only** (idempotent); **never auto-retries non-GET** (a replayed
    create/submit/payment could double-charge). GETs retry within a ~2s budget,
    then throw `BackendUnavailableError`.
  - Transient = 502/503/504 + Cloudflare 520–524. Third-party hosts (GCS
    signed-URL uploads) pass straight through untouched.
- **`components/backend-status-banner.tsx`** — the two UX states; mounted in
  `app/[locale]/layout.tsx` so it covers all customer-facing surfaces (landing,
  logged-in dashboard, review screens) with full i18n. `/admin/*` is intentionally
  not covered (internal, English-only).
- **i18n** — `backendStatus` namespace added to `messages/en.json` and translated
  to all 32 other locales.

### Copy (en)

> **We're having trouble reaching our servers**
> This is usually temporary — please try again in a couple of minutes.
> Any karaoke videos currently being created are unaffected and will keep processing.

## Tests

- `__tests__/backend-status.test.ts` — store transitions + timer escalation.
- `__tests__/api-fetch.test.ts` — GET retry→unavailable, transient-503 retry,
  transparent recovery, non-GET no-retry, GCS passthrough, caller-abort passthrough.
- `lib/__tests__/api.test.ts` — updated `completeJobUpload` assertion (network
  failure now maps to `BackendUnavailableError` preserving `.cause`).
- Banner verified visually (reconnecting + unavailable, en + es) via local dev.
- Full suite: 1129 passing.
