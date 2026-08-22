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
  (`online | reconnecting | unavailable`) + `useBackendStatus()`. Escalates to
  `unavailable` after `UNAVAILABLE_AFTER_MS` (10s) of unbroken trouble; any
  success returns to `online`.
- **`lib/api.ts` — `apiFetch`** wraps every backend call (drop-in for `fetch`;
  internals use `globalThis.fetch`). Design decisions:
  - **Decoupled** the non-aborting "reconnecting" hint (2.5s) from the hard
    backstop (45s) + transient-status/network detection — so a legitimately slow
    GET shows the hint then clears on success and is **never falsely failed**.
  - **Retries GET only** (idempotent); **never auto-retries non-GET** (a replayed
    create/submit/payment could double-charge). GETs retry within a ~10s budget,
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
