# Hardening Plan: Preventing the NOMAD-1632 Class of Incident

**Date:** 2026-09-12
**Author:** Claude (agent), for Andrew
**Status:** Proposal / brainstorm — not yet actioned
**Trigger:** Two linked production incidents on 2026-09-11 → 2026-09-12 (see Case Study)

---

## 0. Resuming this work in a fresh session

**Status: analysis only — nothing here is implemented.** This document is the complete spec; each item is independently shippable.

- **Shipped already (context):** the NOMAD-1632 root-cause fix + the empty-result hotfix are already merged/deployed (v0.222.2 `#982`, v0.222.3 `#983`). This plan is the *follow-up hardening*, not the incident fix.
- **Where this doc lives:** authored in worktree `karaoke-gen-hardening` (branch `feat/sess-20260912-hardening-incident-prevention`), path `docs/archive/2026-09-12-incident-hardening-plan.md`. If it isn't yet on `main`, land it first (docs-only) so every future worktree can see it.
- **How to pick up each item:** start a **fresh worktree per item** (`/startnomad gen <item>`). Do them in the **§5 sequencing** order; each maps to a Tier below with a `where` pointer and grounded `file:line` refs. The items are also filed in workspace `BACKLOG.md` (search "incident-hardening").
- **Key grounded facts to re-verify before coding** (they drove the recommendations): error monitor is spike-based (`error_monitor/monitor.py:229-251`); deploy has no functional gate/rollback and Cloud Run *Jobs* get image-only updates (`ci.yml:1939-1975`); the encode→publish seam has no composed test (`test_video_worker_orchestrator.py:950`); the silent-skip is locked in by `test_gdrive_service.py:619`; a post-`encode()` download failure can still silent-partial-publish (`video_worker_orchestrator.py:636-639`).
- **Two open worktrees** from the incident session (`karaoke-gen-gdrive-720p-gap`, the hotfix branch) can be `/cleanup`'d — their PRs are merged.

---

## TL;DR — if we only do five things

1. **Alert on *any* job failure in near-real-time**, not just error *spikes*. The current error monitor is spike-based and mathematically blind to a brand-new low-volume error — which is exactly why my 2-job outage went unnoticed until Andrew saw the UI. This is the single highest-leverage, lowest-effort net: it catches *every* regression that manifests as a failed job. **(Detection D1)**
2. **Run a functional canary automatically after every backend deploy**, and alert/rollback on failure. The real end-to-end happy-path test already exists (`happy-path-real-user.spec.ts`) — it just isn't wired to deploys; it runs once a day at 06:00 UTC. **(Deploy R1)**
3. **Add one composed integration test of the encode→publish seam** that runs the *real* `GCEEncodingBackend.encode()` through the *real* orchestrator with empty / partial / complete / malformed worker outputs. Both bugs lived in this seam and *no test composes those two layers today.* **(Testing T1)**
4. **Add a publish-boundary completeness invariant, shadow-first.** Before a public NOMAD job is marked `complete`, assert the outputs it *should* have (MP4 + 720p + CDG per job config) are actually present. Ship it in **shadow mode** (log + alert, don't fail) before enforcing. This closes the *silent-partial* class properly and models the safe rollout my guard skipped. **(Guardrails G1)**
5. **Adopt a "new guard" discipline: recoverable ≠ defective, and shadow before enforce.** My fix for a silent bug introduced a loud outage because the new guard (a) conflated an empty/recoverable result with a partial/defective one, and (b) shipped enforce-first with only mocked tests. A short checklist prevents the whole category. **(Process P1)**

The rest of this document is the reasoning, the grounded current-state map, and a fuller prioritized backlog.

---

## 1. Case Study: what actually happened

This was **two linked failures** — and the relationship between them is the most instructive part.

### Failure A — silent partial publish (NOMAD-1632)
A public track (*Nat King Cole – "Portrait of Jennie"*) published to Google Drive **missing its 720p variant**, while the pipeline reported **success**. Root cause: `GCEEncodingBackend.encode()` classified the worker's returned files by scanning the **whole filename** and checked `"portrait"` (to detect the 9:16 portrait video) *before* `"720p"`. The title contains "Portrait", so `… (Final Karaoke Lossy 720p).mp4` matched the portrait branch, `mp4_720p` was never set, and `upload_to_public_share` **silently skipped** the `None` path.

- **Class:** *silent partial success* — the system did less than intended and reported full success.
- **Detection:** the **daily** GDrive validator, **~24h later**. Nothing inline noticed.

### Failure B — the fix became the outage
The fix for A added a **completeness guard**: `encode()` returns `success=False` if the worker's `output_files` is missing any requested format. In production this hard-failed **real user jobs** whose encode returned an **empty** `output_files` — a *stale GCE job cache* condition the orchestrator already recovers from (download 0 files → raise "stale" → re-encode under a fresh id). The guard fired *inside* `encode()` and raised before that recovery could run.

- **Class:** *a safety check that became a new failure mode*, shipped with green CI.
- **Detection:** **Andrew noticed failed jobs in the UI.** No automated signal fired (2 failures ≠ a "spike").
- **Blast radius:** exactly 2 jobs (fresh encodes were unaffected — they produce all 4 formats).

### The meta-lesson

Both failures share **one root process gap**, not two:

> We had **no integration coverage of the real seam**, **no shadow rollout for a new enforcement**, **no post-deploy functional signal**, and **no fast alert on job failure.** Green mocked unit tests gave false confidence in both directions — they didn't catch the silent-partial (A), and they didn't catch that the guard would over-fire on a data shape the mocks never produced (B).

Everything below attacks that gap from five angles: **Testing, Guardrails, Deployment, Detection, Process.**

---

## 2. Current state (grounded map)

### Deployment (`.github/workflows/ci.yml`)
- **Auto-deploy on merge to main.** Deploy jobs are gated only by `if: ref==main && event==push`; they `needs: [package-build]` (frontend has no `needs`). **No deploy job depends on any test job** — on the push-to-main run, all test jobs are skipped (`if: ref != main`). Gating is *purely* PR branch-protection via the `ci-gate` check. A direct push to main would deploy with zero tests. (`ci.yml:821-824`, `:1082-1146`)
- **Post-deploy check = liveness + version string only.** "Verify deployment and version" curls `/api/health` and polls `/api/health/detailed` for the expected `.version`. **No functional verification.** (`ci.yml:1977-2015`)
- **The Cloud Run *Jobs* — where Failure B's code runs — get no verification at all.** `video-encoding-job`, `lyrics-transcription-job`, `audio-separation-job` are updated **image-only** (`--quiet`), sharing the *same* backend image; no health check, no smoke, no rollback, picked up on next invocation. (`ci.yml:1939-1975`)
- **No automatic rollback** for the Cloud Run *service*: `gcloud run deploy` ships a new revision at 100% traffic (no `--no-traffic`/canary). If the verify step fails, the bad revision keeps serving. A human rollback (`gcloud run services update-traffic … --to-revisions=<prev>=100`) is **not codified** anywhere. (`ci.yml:1911-1937`)
- The **GCE encoding worker** (preview encodes) *does* have blue-green + a "deep health check - real encode test" + rollback — but it's `continue-on-error` and only protects the *preview* worker, **not** `video-encoding-job`. (`ci.yml:1443-1580`)
- **Real end-to-end tests run daily (06:00 UTC) or on an opt-in PR label**, never post-deploy. (`e2e-daily.yml:7-8`; `ci.yml:237-304`)
- **Ephemeral self-hosted runners** are fire-and-forget; a failed VM insert silently strands a queued job (relies on GitHub redelivery + a 15-min orphan sweep). This is the "stuck queued" pain we hit repeatedly this session; also recurring disk-pressure and GPU-quota stranding. (`infrastructure/functions/runner_manager/ephemeral.py:441-461`, `:576-586`)

### Detection / observability
- **Error monitor is spike-based** and cannot fire on a novel low-volume error: `_is_spike` requires `current_count >= SPIKE_MIN_COUNT` **and** `current_count > rolling_avg × MULTIPLIER`; a brand-new error has `rolling_avg == 0.0 → return False`. (`backend/services/error_monitor/monitor.py:229-251`) **This is precisely why Failure B was invisible to automation.**
- **GDrive validator** (metadata gap/dup/format checks) runs **daily at 21:00** + a **5-min post-job** Cloud Tasks trigger. It was the *only* detector for Failure A, and history records "~24h late." (`infrastructure/functions/gdrive_validator/main.py`; `docs/GDRIVE-VALIDATOR.md:48-51,302,306`)
- **`job_health_service.check_job_consistency`** catches stuck-in-status timers and flag mismatches, but **has no "completed job has all its outputs" rule.** (`backend/services/job_health_service.py:67,123-168`)
- Health endpoints are connectivity-only.

### Testing
- **The encode→publish seam is only covered in isolation.** `test_run_encoding_gce_stale_cache_retries` mocks the *entire* backend (`_get_encoding_backend` patched; `mock_backend.encode` returns a full `EncodingOutput`), so the **real `GCEEncodingBackend.encode()` and its guard never execute in an orchestrator test.** The empty/cached shape is fed to the guard only in a *unit* test, and the recovery only in a *whole-backend-mocked* test. **Nothing composes real-encode(empty) → real-orchestrator → re-encode.** (`backend/tests/test_video_worker_orchestrator.py:950-1022`; `backend/tests/test_encoding_interface.py:433`)
- **All encoding mocks are hand-authored; no captured real-worker fixtures.** There's no typed schema for the worker's raw response (`encode_videos` returns an untyped dict/list handled defensively). `docs/TESTING.md:161-213` explicitly warns "Mocks Must Match Real API Contracts" / "Test the Contract Between Caller and Callee" — but nothing enforces it. `test_encoding_interface_contract.py` is a contract test in name only (it's about countdown padding).
- **The silent-skip is locked in by a test.** `test_upload_to_public_share_skips_missing_files` asserts a partial input → 1 upload, `len(result)==1`, **no error**. So "publish whatever's present, don't complain" is intended behavior with a test guarding it — and **nothing asserts all-3 completeness anywhere in the unit/integration/emulator tiers.** (`backend/tests/test_gdrive_service.py:619-659`)
- **The residual silent-partial hole is still open.** A `None` slot arising *after* `encode()` — e.g. a per-file download failure in `_download_gce_encoded_files` clears one result attr (`:636-639`) while others succeed — flows into `_upload_to_gdrive` and publishes a partial release **without tripping the new guard.** My guard only covers partials *at the encode() boundary.*
- **The emulator "integration" suite is API-CRUD only** — it mocks all workers (`trigger_video_worker = AsyncMock`), so no job is ever driven through real encoding/distribution. (`backend/tests/test_emulator_integration.py:72-80`)
- **Even the E2E completeness check is incomplete:** `happy-path-real-user.spec.ts` STEP 10.5 checks `lossless_4k_mp4`, title/end mov, input audio — but **not `lossy_720p_mp4` or CDG** — so it would *not* have caught NOMAD-1632. (`:1025-1069`)

---

## 3. Prioritized recommendations

Each item: **what**, **why (tie to incident)**, **effort** (S/M/L), and a **where** pointer.

### Tier 1 — universal nets (do first; highest leverage/effort ratio)

#### D1. Near-real-time alert on *any* job failure  — effort **S**
- **What:** On any job transition to `FAILED`, emit an immediate alert (Discord + email) with job id, artist/title, status-at-failure, and the error message; dedupe by a normalized error signature so a burst collapses to one alert with a count. Independent of the spike detector.
- **Why:** The error monitor is spike-based and provably cannot fire on a novel low-volume error (`rolling_avg==0 → not a spike`). Failure B was 2 jobs with a brand-new error string → invisible. A per-failure alert would have paged within minutes. This is the **universal net for every failure-manifesting regression**, present and future.
- **Where:** hook in `backend/services/job_manager.py` at the failed-status transition, or a 5-min scheduled Cloud query for recent `status==failed` jobs → `error_monitor/discord.py`. Reuse the normalizer for dedup. Add a "first time we've ever seen this error signature" flag → always alert on novel signatures regardless of count.

#### R1. Post-deploy functional canary (+ alert / auto-rollback)  — effort **M**
- **What:** After the backend Cloud Run deploy + Cloud Run Jobs update, automatically run the existing production happy-path (`happy-path-real-user.spec.ts`, admin-credit variant to avoid Stripe) as a required post-deploy step. On failure → loud alert and (Tier 3) auto-rollback.
- **Trigger scope — only when the *backend* version actually changed.** Do **not** run the canary on docs-only, frontend-only, or infra-only merges, nor when the backend `poetry version` is unchanged from the currently-serving revision. A real karaoke generation is slow and consumes real resources (encode VM time, a YouTube/Drive/Dropbox publish, cleanup); running it on every merge is wasteful and noisy. Gate it on a computed "backend image/version changed" signal.
  - *Implementation note:* the pipeline already knows the version (`poetry version --short`, `ci.yml:1139-1146`) and the PyPI job already does a "does this version already exist? skip" check (`ci.yml:940-959`) — reuse that same version-changed signal to gate the canary. A backend deploy that ships an identical version (e.g. a frontend-only PR that still runs `deploy-backend`) should short-circuit the canary. Consider also skipping when `Detect Changes` shows no backend/worker path touched.
- **Why:** Both failures reached prod and stayed until a human/validator noticed; the deploy's only gate is a version string + `/api/health`. The capability already exists — it's just scheduled daily, not deploy-triggered. A gross breakage (e.g. *every* job failing) would be caught in minutes instead of up to 24h.
- **Caveat (important):** a *fresh* happy-path job would **not** have reproduced Failure B (only stale-cache/empty jobs failed) nor Failure A (only colliding titles). So R1 catches gross regressions but must be **paired with T1/T2** for edge/recovery-path and data-dependent bugs. Extend the canary to also drive an **admin reset + retry** of a throwaway job (exercises the stale-cache/re-encode path that Failure B broke).
- **Where:** new job in `ci.yml deploy-backend` (or a `deploy-verify` job `needs: [deploy-backend]`, conditioned on the version-changed signal) invoking the production Playwright config; reuse `e2e-daily.yml`'s auth-token refresh.

#### T1. One composed integration test of the encode→publish seam  — effort **M**
- **What:** A test that runs the **real** `GCEEncodingBackend.encode()` through the **real** orchestrator `_run_encoding` (mocking only the network boundary — `encode_videos` — and GCS), asserting:
  - **empty** `output_files` → `success=True` (no guard trip) → orchestrator downloads 0 → raises "stale" → **re-encodes** (the Failure-B path);
  - **partial** (some formats, 720p missing) → guard trips → job fails loud (the Failure-A defense);
  - **complete** → success, all attrs populated;
  - **malformed** (stray/wrong-extension names) → classified out, treated as incomplete/empty appropriately.
- **Why:** This is the exact seam both bugs lived in, and **no current test composes these two layers.** A single composed test would have caught B before merge (empty → must recover, not fail) and A (partial → must fail).
- **Where:** `backend/tests/test_video_worker_orchestrator.py`, a new `TestEncodePublishSeam` class; do **not** patch `_get_encoding_backend` — patch only `service.encode_videos` and `storage`.

### Tier 2 — close the silent-partial class properly

#### G1. Publish-boundary completeness invariant, **shadow-first**  — effort **M**
- **What:** Before marking a public NOMAD job `complete`, compute the *expected* output set from the job config (always MP4 + 720p; CDG iff `enable_cdg`; etc.) and compare against what was actually distributed (`gdrive_files` / `file_urls`). Log + alert on any shortfall. **Ship in shadow mode first** (record + alert, never fail the job) for ~1–2 weeks; only then decide whether to enforce (block completion / auto-retry).
- **Why:** This closes the **residual hole** my guard leaves open — a `None` slot arising *after* `encode()` (download-failure path at `_download_gce_encoded_files:636-639`) still publishes a partial today. It also puts the check at the **right altitude** (the publish boundary, aware of what *should* ship) instead of deep in `encode()`. Crucially, shadow-first is the rollout discipline that would have prevented Failure B entirely.
- **Where:** end of `_run_distribution` in `video_worker_orchestrator.py:680-721`, reading effective distribution config; alert via the D1 channel. Add the missing formats to the E2E STEP 10.5 completeness check (`720p`, CDG) at the same time.

#### D2. Make silent-partial detection same-day, and verify the post-job validator fires  — effort **S–M**
- **What:** Confirm the 5-min post-job GDrive validator trigger actually ran for NOMAD-1632 (history says it was caught ~24h later, implying it didn't flag — likely because 1632 wasn't the global-max at publish time, so no *gap* was computed until a later track raised the max). Fix so a just-published track is validated for *its own* completeness (all three folders contain *this* brand code), independent of global-max gap logic.
- **Why:** Even with G1, defense-in-depth wants the metadata backstop to catch same-day, not next-day.
- **Where:** `infrastructure/functions/gdrive_validator/main.py:253-261` (add a "this brand code present in all expected folders" check on the post-job path).

### Tier 3 — deployment / rollout robustness

#### R2. Codified, fast rollback for Cloud Run service **and** Jobs  — effort **S**
- **What:** A one-command script + a documented runbook to roll the `karaoke-backend` service *and* the three Cloud Run Jobs back to the previous image (`:v<prev>` / prior SHA). Optionally a `/rollback` slash command.
- **Why:** There is no automatic rollback and the manual path isn't written down anywhere; during an outage we should not be composing `gcloud` from memory. The Jobs (where Failure B ran) especially have zero rollback today.
- **Where:** `scripts/rollback.sh` + a section in `docs/TROUBLESHOOTING.md`; images are already addressable by SHA/version (`ci.yml:1809-1812`).

#### R3. Canary the Cloud Run *service* (traffic-gated)  — effort **M**
- **What:** Deploy the new service revision with `--no-traffic --tag=candidate`, run a smoke against the tagged URL, then migrate 100% only on pass; auto-revert the tag on failure.
- **Why:** Turns "bad revision serves 100% immediately" into "bad revision serves 0%." Note this only helps the *service*; Cloud Run **Jobs can't traffic-split**, so Jobs still rely on R1's real-job canary + R2 rollback.
- **Where:** `ci.yml deploy-backend` deploy step (`:1911-1937`).

#### R4. Put a functional gate between merge and 100% traffic  — effort **M**
- **What:** Either gate the push-to-main deploy on R1's canary, or (given deploy latency) deploy → canary → auto-rollback-on-fail. At minimum, make the deploy job *fail the workflow* (and page) when the post-deploy check fails, which today it does for the version check but not functionally.
- **Why:** Today deploy is unconditional post-merge with no functional gate.

### Tier 4 — process & guard discipline

#### P1. "New guard / new enforcement" checklist (shadow-first)  — effort **S**
Codify (in `docs/TESTING.md` or a `/new-guard` prompt) that any new enforcement on a critical path MUST:
1. **Distinguish "nothing yet / recoverable" from "wrong result."** (Failure B conflated empty-recoverable with partial-defective.)
2. **Default to prior behavior on uncertainty** (fail *open* unless you're sure it's a defect).
3. **Be tested against empty / partial / malformed / complete** inputs — not just the happy shape.
4. **Ship in shadow mode first** (log + alert), enforce only after a clean shadow window. (Precedent: the timing gate's "G3 shadow" rollout.)
5. **Have a composed integration test**, not only isolated unit mocks.

#### P2. Critical-path change tier + blameless post-mortem  — effort **S**
- Tag changes to encode / publish / distribution as "critical path" → require T1-style composed test + G1-style shadow + R1 canary before enforcing.
- Adopt a lightweight blameless post-mortem template (this document is effectively the first one); keep a running incident log in `docs/`.

#### P3. Kill mock drift with fixtures + a typed contract  — effort **M**
- **What:** Capture *real* GCE worker responses (success, cached, **empty**, partial) as golden fixtures under `backend/tests/fixtures/encoding/`; add a periodic contract test that replays them through `classify_encoded_output` / `encode()`. Introduce a typed schema (TypedDict/dataclass) for the worker's raw response so caller and worker share one definition.
- **Why:** Every encoding mock is hand-authored and the real "cached/empty" shape was simply never in the corpus — the textbook cause of Failure B. `docs/TESTING.md` already preaches this; nothing enforces it.

### Tier 5 — CI reliability (trustworthy signal + fast response)

#### C1. Fix ephemeral-runner stranding  — effort **M**
- Await/confirm the VM insert (or reconcile queued `workflow_job`s against created VMs) instead of fire-and-forget, so a failed insert doesn't silently strand a job for up to 15 min. (`ephemeral.py:441-461`) This directly caused the repeated "stuck queued Backend Unit" delays while shipping the hotfix — CI unreliability *lengthens incident response*.

#### C2. De-flake the E2E smoke  — effort **S**
- The smoke's `page.waitForLoadState('networkidle')` timed out (120s) on the marketing landing page and failed a deploy-run; replace `networkidle` with `domcontentloaded` + explicit element waits. Flaky smokes train us to ignore red, which is dangerous when red is real. (`frontend/e2e/regression/smoke.spec.ts:30-53`)

#### C3. Disk-pressure headroom on runner images  — effort **S**
- Recurring `>85%` disk hard-fails and Docker cold-boot races point at undersized/rarely-rebuilt runner images. Bump disk / rebuild cadence.

---

## 4. How each recommendation maps to the two failures

| Recommendation | Would have caught Failure A (silent partial) | Would have caught Failure B (guard over-fired) |
|---|---|---|
| D1 job-failure alert | — (A was a false *success*) | ✅ within minutes |
| R1 post-deploy canary (+ retry variant) | only if canary song collided / retry variant added | ✅ if the retry variant exercised stale-cache |
| T1 composed seam test | ✅ (partial → fail asserted) | ✅ (empty → must recover, not fail) |
| G1 publish-completeness invariant (shadow) | ✅ (720p missing at publish) | ✅ (shadow-first prevents over-fire) |
| D2 same-day validator | ✅ faster | — |
| P1 guard discipline | — | ✅ (recoverable≠defect, shadow-first) |
| P3 fixtures/contract | ✅ (real shapes in corpus) | ✅ (empty shape tested) |

**Nothing single-handedly catches both.** The combination — a universal failure alert (D1) + a composed seam test (T1) + a shadow-first publish invariant (G1) + guard discipline (P1) — closes the gap from four independent directions, which is the point of defense in depth.

---

## 5. Suggested sequencing

1. **This week (S, high value):** D1 (failure alert), R2 (rollback runbook/script), C2 (de-flake smoke), P1 (guard checklist).
2. **Next (M):** T1 (composed seam test), G1 (publish invariant in **shadow**), R1 (post-deploy canary).
3. **Then (M):** P3 (fixtures/contract), D2 (same-day validator), R3/R4 (service canary + gate), C1 (runner stranding).
4. **Decide after shadow data:** whether G1 enforces (block/auto-retry) or stays alert-only.

Each is independently shippable; none requires a big-bang rewrite.
