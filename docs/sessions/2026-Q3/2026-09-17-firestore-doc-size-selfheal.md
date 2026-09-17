# Firestore job-doc 1MB self-heal (review submit 500) — 2026-09-17

**Project:** karaoke-gen   **Branch/commit:** merged to `main` via PR #1013 (`20f5b581`), v0.228.0   **Status:** done — shipped & verified in prod

## Summary
A customer (job `d93747cd`, `karaokehunt@nomadkaraoke.com` batch holding account) couldn't submit their lyrics review. Clicking **Complete Track** 500'd with Firestore's *"Document … cannot be written because its size (1,048,592 bytes) exceeds the maximum allowed size of 1,048,576 bytes."* The browser console blamed `POST /api/jobs/d93747cd/corrections` (106KB body), but that endpoint was innocent — it already persists only a tiny `{'corrected_segment_count': N}` summary to Firestore.

Root cause: the blocker was a **different field**. Measuring per-field sizes of the actual doc showed a **1.19MB embedded `worker_logs` array** (total doc 1.21MB). Once any field pushes a doc to ~1MB, *every* subsequent write fails, so the error surfaces on whatever the user clicks next. `worker_logs` is deprecated (logs now live in the `jobs/{id}/logs` subcollection, `USE_LOG_SUBCOLLECTION=true` by default), but the legacy dict-based `FirestoreService.append_worker_log` ignored the flag and kept appending to the embedded array — the review-flow `JobLogger`/`FirestoreJobLogHandler` in `backend/services/job_logging.py` used exactly that path.

## What changed
Code (all in `backend/services/firestore_service.py`):
- **Stop new bloat:** `append_worker_log` (dict form) now routes to the `jobs/{id}/logs` subcollection when `USE_LOG_SUBCOLLECTION` is on (default in prod) instead of `ArrayUnion`-ing into the embedded array.
- **Self-heal existing bloat:** new `_update_with_size_recovery(doc_ref, updates, job_id)` helper wraps the writes in `update_job` and `update_job_status`. On a `"maximum allowed size"` error it does `doc_ref.update({'worker_logs': firestore.DELETE_FIELD})` then retries the caller's update once. Guarded: skips recovery if the write itself sets `worker_logs`, and re-raises any non-size error untouched. (`_is_doc_size_error` matches the message substring.)
- Version bump `0.227.0 → 0.228.0`; added a LESSONS-LEARNED entry.

Tests: `backend/tests/test_worker_log_subcollection.py` — 6 new cases (recovery clears+retries; non-size errors re-raise; a `worker_logs` write doesn't recurse; `update_job_status` recovery; append routing on/off). 120 passed across related suites (services, job_manager, jobs_corrections).

Prod ops actions:
- Shipped via PR #1013 → merged → deployed (revision `karaoke-backend-01003-hem`, image `20f5b581`).
- **Verified in prod** by reproducing the exact "Complete Track" flow: `POST /api/review/d93747cd/complete` (Bearer admin token, `instrumental_selection="with_backing"`, correction data fetched live from `/api/review/d93747cd/correction-data`) → **HTTP 200** `{"status":"success","instrumental_selection":"with_backing"}`. Doc dropped **1,217,764 → 27,920 bytes**, `worker_logs` field gone, status advanced `in_review → review_complete`, logs subcollection now populated.

## Decisions & rationale
- **Self-heal in the central `firestore.update_job`/`update_job_status`** rather than in the corrections endpoint — protects ALL job-doc writes, and unblocks already-bloated docs automatically on their next write (ADC is read-only `claude-readonly@`, so I couldn't clear the field out-of-band; the fix had to run in-app).
- **Route the legacy dict `append_worker_log` through the flag** rather than deleting it — keeps the two remaining callers (`JobLogger`, `FirestoreJobLogHandler`) working while stopping the bloat at source.
- **Verified via API, not full browser drive** — the fix is entirely backend; the `/complete` endpoint is what the button hits, so a faithful HTTP repro exercises the exact changed path. Completing the job (and triggering render) was the explicit verification Andrew asked for.

## Learnings / gotchas
- **When a Firestore write fails on doc size, don't assume the payload you're writing is the culprit** — measure per-field sizes of the *existing* doc first: `len(json.dumps(v, default=str))` over `doc.to_dict().items()`.
- **`api.nomadkaraoke.com` WAF blocks `python urllib` POSTs** → 403 Cloudflare error code 1010 (bot signature). `curl` (browser-ish UA) passes. GETs via curl were fine; the urllib POST tripped it. See `project_edge_security_cloudflare`.
- **Deploy wedged on `Package - Build` queued** (~12 min, runner never registered) — the known GPU-runner single-dispatch stall, NOT a code problem. GPU quota was 0 (free), so `gh run cancel <id>` + `gh run rerun <id>` forced a fresh dispatch and it picked up in ~1 min. See `project_gha_runner_stall_single_dispatch`.
- The instrumental "Complete Track" modal posts the full ~100KB corrections to `/api/review/{id}/complete` (NOT `/jobs/{id}/corrections`); both funnel Firestore writes through `firestore.update_job` (via `update_state_data`/`update_file_url`), so both are now protected.
- CodeRabbit CLI 401s in this org (life360 seat) — opened the PR *without* `@coderabbitai ignore` so the GitHub bot reviewed (it was rate-limited but non-blocking).

## Open threads & next steps
- **Durable follow-up (not done):** other old job docs may still carry bloated `worker_logs`; they now self-heal lazily on their next write. If a proactive sweep is ever wanted, a one-off admin job could `DELETE_FIELD` `worker_logs` across docs — but not required.
- Job `d93747cd` is now `review_complete` and should render normally; no further action expected.

## Related docs
- Memory: `project_gen_firestore_doc_size_selfheal`
- `docs/LESSONS-LEARNED.md` — "The Blocker Can Be a Different Field Than the One Being Written (Sep 2026)"
- Prior related: `project_gen_file_urls_state_data_race` (allowlist-summary in submit_corrections), `project_edge_security_cloudflare`, `project_gha_runner_stall_single_dispatch`
