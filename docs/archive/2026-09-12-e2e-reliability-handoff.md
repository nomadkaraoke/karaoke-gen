# Handoff: Make the production E2E tests reliably green

**Date:** 2026-09-12
**Author:** Claude (agent), for the next session
**Status:** ✅ RESOLVED 2026-09-13 — see the resolution note directly below
**Scope:** karaoke-gen production E2E suites + the R1 post-deploy canary

---

## RESOLUTION (2026-09-13, PR #992)

The §3 hypothesis (review page loads empty → `hasNoLyrics` → Proceed disabled)
was **disproven** by run 34729291204's artifacts: the review loaded fine and the
Playwright error was "waiting for getByRole" — the button was **absent**, not
disabled. Real root cause: the finish modal's CTA is **nondeterministic per
job**. When the auto-scorer's backing verdict is confident and/or both stems are
transcoded, `ReviewChangesModal` renders **"Complete Track"** (`completesReview`,
inline instrumental chooser — completes the whole review; the `/instrumental`
screen never appears) instead of **"Proceed to Instrumental Review"**. The spec
only knew the latter. Same v0.223.2 passed the 22:15 UTC canary (non-confident
branch) and failed later runs (confident branch) — scorer confidence varies with
each fresh transcription/separation.

**Fix (test-side, PR #992):** the spec accepts either CTA (waits for the `/app`
redirect on "Complete Track" and skips Step 8, including the product's
completeReview-failure fallback to `#/instrumental`), handles the C1 mirror skip
(lyrics-confident → hash-redirect straight to instrumental), replaces
instant-snapshot `isVisible/isEnabled({timeout})` gates with real auto-waits,
and fixes the always-firing "Review page content not found" reload (strict-mode
violation from a multi-match `.or()` union).

**DoD status:** 3 consecutive green dailies post-merge (runs 34732524778 —
"Complete Track" branch; 34735803530 — "Proceed" branch; 34737171527 — "Complete
Track"), covering both CTA variants. Tenant E2E green (34730560382, §4 one-off
confirmed transient). Canary reuses the fixed spec, so criterion 3 is expected
green on the next version-bumped deploy. Residual (separate, retry-mitigated):
run 34732524778's first attempt hit a Step-9 "Timeout waiting for job
completion" (render+encode exceeded the 35-min window); not related to the CTA
bug. Memory: `project_gen_e2e_happy_path_cta_flake`.

---

## 0. TL;DR for the next session

Three production E2E surfaces exist. After this session's work, **two of the three
root causes are fixed**; **one remains** and is the real job here:

| Surface | Workflow | State entering this handoff |
|---|---|---|
| Daily payment (Stage 1) | `.github/workflows/e2e-daily.yml` → "Stage 1: Credit Purchase" | ✅ **FIXED** — see §2 |
| Daily happy-path (Stage 2) | `e2e-daily.yml` → "Stage 2: Happy Path" (`frontend/e2e/production/happy-path-real-user.spec.ts`) | 🔴 **FLAKY — the main task** (§3) |
| Tenant portals | `.github/workflows/e2e-tenant-daily.yml` ("E2E Daily Test (Tenant Portals)") | 🟢 healthy (one-off 2026-09-11), re-verify (§4) |
| R1 post-deploy canary | `ci.yml` job `deploy-verify` (reuses the happy-path spec) | ⚠️ inherits the Stage-2 flake (§3) — mitigated, not cured |

**Your mission:** root-cause and fix the Stage-2 happy-path flake so the daily E2E
**and** the post-deploy canary go reliably green, then confirm the tenant suite is
still green. Success = several consecutive green daily runs + a green canary on the
next real version-bumped deploy, with no false Discord pages.

**Do NOT** "fix" this by widening timeouts blindly or by disabling the assertion.
The failure is a real intermittent **review-page data-load** problem (details below);
find out *why the review UI intermittently loads with no lyrics/corrections*.

---

## 1. Context — what shipped just before this (so you're not confused by recent history)

All merged + deployed to prod on 2026-09-12, prod healthy at **v0.223.2**:
- **Incident hardening (post-NOMAD-1632)** — PRs #985/#986/#987. Plan:
  `docs/archive/2026-09-12-incident-hardening-plan.md`. Added: D1 near-real-time
  job-failure alert (`backend/services/ops_alerts.py`), G1 publish-completeness
  shadow (`backend/services/publish_completeness.py`), D2 same-run gdrive validator,
  R1 post-deploy canary + R2 rollback script + R3/R4 traffic-canary + C1 runner fix +
  C2 smoke de-flake.
- **Canary version-gate fix** — PR #988. The R1 canary was firing on no-bump deploys
  (it probed the raw Cloud Run origin `/api/health/detailed`, got empty, fail-opened).
  Now it reads the version via the **edge** (`https://api.nomadkaraoke.com`) with
  retries → correctly **skips** the canary when the version is unchanged. Verified.
- **E2E payment cost/card** — PRs #989 (+ #990). See §2.
- **Happy-path `retries: 1`** — PR #990 (see §3).

**None of these changed the review/preview flow.** The Stage-2 flake predates them
(the identical v0.223.2 code passed the happy-path canary at ~18:52 local on
2026-09-12, then failed twice ~21:00/21:23) — so it is **not** a regression from this
work.

---

## 2. ✅ FIXED: Stage 1 (credit purchase) — was failing 6 days straight

**Root cause:** the daily payment test did a *real* Stripe charge on a **personal
card** whose owner (Andrew) had frozen it → checkout never redirected to
`/payment/success` → 60s timeout at `frontend/e2e/helpers/stripe-checkout.ts:236`.
(6 consecutive daily failures 2026-09-07 → 09-12 were all this, at Stage 1, so Stage 2
rarely even ran — that's why the Stage-2 flake was masked.)

**Fix (shipped):**
- Referral code switched `e2etest70` (70% off, ~$3) → **`e2etest95` (95% off → $0.50)**.
  `$10` base × 5% = **$0.50 = Stripe's USD minimum charge** (a literal $0.10 is
  impossible). `e2etest95` was **created in prod** (vanity referral link, owner
  `admin@nomadkaraoke.com`, 95% off, 0% kickback, enabled) via
  `POST /api/referrals/admin/vanity`. `frontend/e2e/production/credit-purchase-real.spec.ts`
  now uses `?ref=e2etest95` and asserts the 95% badge.
- Andrew re-pointed the GitHub secrets `E2E_STRIPE_CARD_NUMBER/EXPIRY/CVC/CARDHOLDER_NAME`
  (+ `E2E_STRIPE_ZIP`) at the **Nomad debit card**.

**Verified:** Stage 1 passed on two consecutive manual runs on 2026-09-12 (runs
`34729291204` and `34730689994`), charging ~$0.50 to the Nomad debit card. **Nothing
to do here** unless it regresses. Ongoing cost ≈ $0.50/day (~$15/mo).

---

## 3. 🔴 THE MAIN TASK: Stage 2 (happy-path) review-page load flake

### Symptom (from the failing runs, e.g. run `34729291204` both original + `--failed` rerun)
```
Waiting for lyrics transcription (this may take 10-20 minutes)...
WARNING: Review page content not found, reloading...
Waiting for preview video generation...
Preview generation complete
WARNING: No video element found, but continuing anyway
WARNING: Proceed button not ready (missing/disabled) after preview — recovering (reload review + reopen preview)...
Re-opened preview modal after recovery
✘  expect(getByRole('button', { name: /proceed to instrumental/i })).toBeEnabled()  — Timeout 30000ms
   > happy-path-real-user.spec.ts:764
```

### What's actually going on (grounded)
- The button the test waits on (`frontend/e2e/production/happy-path-real-user.spec.ts:749-764`)
  is the **ReviewChangesModal** footer button — `frontend/components/lyrics-review/modals/ReviewChangesModal.tsx:267-283`.
- That button is `disabled={isSubmitting || hasNoLyrics}` (`ReviewChangesModal.tsx:269`).
  So a persistently-disabled Proceed button ⇒ **`hasNoLyrics` is true** ⇒ the review UI
  believes there are **no lyrics/corrections loaded**.
- The earlier log line **"Review page content not found, reloading…"** is the smoking
  gun: the review page intermittently renders **without its lyrics/corrections data**.
  The preview stuff ("No video element found") is a **red herring** — the test explicitly
  continues past a missing preview (`spec.ts:727,733`); proceeding to instrumental does
  NOT depend on the preview. The real gate is the lyrics/corrections data load.
- **Not infra-down:** the preview/encoding worker `encoding-worker-a` was healthy during
  the failures (`/health` → `{"status":"ok","queue_length":0,"wheel_version":"0.223.2"}`).
- **Intermittent:** the same v0.223.2 passed the canary at ~18:52, then failed ~3 of 4
  happy-path runs later that evening. High failure rate that evening, but genuinely
  intermittent (not 100%).

### The hypothesis to prove/disprove first
> The `/app/jobs/#/{jobId}/review` UI sometimes finishes loading before (or without)
> its lyrics-review/corrections payload, so `hasNoLyrics` is true and Proceed stays
> disabled. Likely a **data-fetch race / timing** on the review session load (frontend
> fetch of corrections, or the backend review-session endpoint returning empty/late),
> possibly interacting with the "content not found → reload" recovery the test does.

Where to look:
- Frontend review load: `frontend/components/lyrics-review/**` — find what populates the
  lyrics/corrections state and what drives `hasNoLyrics` (trace the prop back from
  `ReviewChangesModal`). Identify the fetch (review session / corrections JSON) and its
  loading/empty states. Is there a window where the page renders "ready" but the
  corrections are still empty/loading?
- Backend review session data: the endpoint(s) that serve the review corrections
  (search `backend/api/routes/review.py`, `backend/services/**` for the review session /
  corrections fetch). Does it ever 200-with-empty or 404 transiently right after
  transcription completes (a readiness race, cf. the vocals-waveform 202/Retry-After
  pattern in memory `project_gen_vocals_waveform_separation_race`)?
- The test's own recovery (`spec.ts:751-763`) reloads the review page + reopens preview,
  but does NOT explicitly re-wait for lyrics/corrections to be present before checking
  Proceed — if the reload also races, recovery won't help. Consider whether the correct
  fix is **product-side** (make the review UI not report ready until corrections load)
  vs **test-side** (wait for a concrete "lyrics loaded" signal before asserting Proceed).

### How to reproduce / investigate (you have the access)
1. **Re-run the daily** (cheap now, ~$0.50) and watch Stage 2:
   `gh workflow run e2e-daily.yml --repo nomadkaraoke/karaoke-gen`
   then download artifacts (screenshots `07d-preview-ready.png`, `test-failed-*.png`,
   the HTML report, and **video recordings**) from the run — the video shows exactly
   what the review page looked like when Proceed was disabled.
2. **Run the happy-path spec locally against prod** for fast iteration (admin-credit
   variant, no Stripe) — see `CLAUDE.md` "Testing in Production" + `docs/TESTING.md`
   "Ad-Hoc Production Debugging". Token:
   `export KARAOKE_ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)`
   and `frontend/e2e/helpers/debug-prod-template.mjs`.
3. **Drive a real job to the review screen interactively** (admin token, `/vis` skill /
   Chrome) and hard-refresh the review page repeatedly to catch the "loads with no
   lyrics" state; watch the Network tab for the corrections/review-session fetch
   (status, timing, empty body). Best done with some daytime traffic / a freshly
   transcribed job.
4. Cross-check prod logs for review-session/corrections fetch errors around the failing
   run timestamps (`gcloud logging`), and the error monitor / the new D1 failure alerts.

### Likely fix shapes (decide after repro — don't guess-ship)
- **Product:** make the review page treat "corrections still loading/empty" as a
  not-ready state (spinner) rather than rendering an empty review whose Proceed is
  silently disabled; or have the corrections fetch retry/wait-for-readiness (202 +
  Retry-After style) instead of returning empty right after transcription.
- **Test:** before asserting Proceed enabled, wait for a concrete "lyrics rendered"
  locator (e.g. a lyrics line / segment element), and make the recovery reload re-wait
  for that. This is legitimate if the product behavior is acceptable for real users
  (they'd just wait/refresh) — but confirm real users aren't actually blocked first.

Prefer the **product** fix if real users can hit the same empty-review state; prefer the
**test** fix only if it's purely a test-timing artifact. Verify which by repro in a real
browser.

### Mitigation already shipped (so you're not starting from a paging storm)
- **`retries: 1`** on the happy-path (`spec.ts:159`, PR #990) — a single flake now
  self-heals instead of hard-failing/paging the daily + canary. This is a band-aid,
  **not** the fix; the underlying load flake still needs solving. (Note: the spec's
  `test.describe.configure({ retries })` **overrides** the prod config's `retries: 2`
  AND any `--retries` CLI flag — that's why the canary's `--retries=2` did nothing
  before #990.)

---

## 4. 🟢 Tenant E2E — was a one-off, re-verify

- Workflow: `.github/workflows/e2e-tenant-daily.yml` → "E2E Daily Test (Tenant Portals)"
  (runs `frontend/e2e/production/tenant-happy-path.spec.ts` for `vocalstar` + `singa`).
- The 2026-09-11 failure Andrew emailed about (run `34562431607`, #104) was **vocalstar
  only** (`singa` passed same run): `locator('#tenant-mixed-audio').setInputFiles` timed
  out 60s (`tenant-happy-path.spec.ts:146`) — the mixed-audio upload field didn't appear
  in time. `#tenant-mixed-audio` is rendered unconditionally by `FileDropZone` in
  `frontend/components/job/TenantJobFlow.tsx:307`, so this reads as a **transient
  render/timing** blip, not a config gap.
- **History:** green every day 2026-09-05 → 09-10, the 09-11 one-off, then **green again
  09-12** (run `34673013842`). So it's currently healthy.
- **Action:** just confirm it's still green (a re-run was triggered at end of the prior
  session: run `34730560382`). If `vocalstar` flakes again on the same `setInputFiles`
  step, apply the same "wait for a concrete ready signal / retry" treatment as §3 rather
  than widening the raw timeout.

---

## 5. Success criteria (definition of done)

1. **Stage 2 happy-path** roots-caused and fixed (product and/or test), with a written
   explanation of *why* the review loaded empty.
2. **Daily E2E** ("Payment + Happy Path") green on **≥3 consecutive** runs (manual
   triggers are fine; ~$0.50 each).
3. **Post-deploy canary** green on the next real version-bumped backend deploy (it
   reuses the happy-path spec, so §3's fix fixes it too).
4. **Tenant E2E** confirmed green (≥1 clean run).
5. No false Discord pages from any of the above in a normal week.

## 6. Ops facts / access you'll need

- **Prod:** `gen.nomadkaraoke.com` (frontend), `api.nomadkaraoke.com` (backend), v0.223.2.
- **Admin token:** `gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1`.
- **Run/inspect workflows:** `gh run list --workflow e2e-daily.yml`, `gh run view <id> --log`,
  `gh run download <id>` (screenshots + video), `gh workflow run <file>`.
- **E2E payment:** code `e2etest95` (95% off) live in prod; `E2E_STRIPE_*` secrets =
  Nomad debit card; each Stage-1 run charges ~$0.50 (real). Don't crank the discount to
  100% (Stripe rejects <$0.50 and a $0 checkout skips card capture, changing coverage).
- **Preview/encoding worker:** `encoding-worker-a` (us-central1-c); health:
  `gcloud compute ssh encoding-worker-a --zone=us-central1-c --project=nomadkaraoke --command="curl -s http://localhost:8080/health"`.
- **Relevant memories:** `project_gen_vocals_waveform_separation_race` (202/Retry-After
  readiness pattern), `project_gen_preview_video_async`, `project_gen_lyrics_review_local_test_harness`,
  `project_gen_full_auto_review`.

## 7. Status of the two runs in flight when this doc was written (2026-09-12 ~21:30 local)
- Daily `34730689994`: Stage 1 ✅ (paid $0.50), Stage 2 running with `retries: 1`.
- Tenant `34730560382`: queued.
Check their outcomes first — they're the freshest data points for §3/§4.
