# Tenant Portal E2E — Rewrite as a Faithful Frontend Journey Test

**Date:** 2026-07-04
**Status:** Design approved, pending spec review → implementation plan
**Author:** Andrew Beveridge + Claude
**Scope:** karaoke-gen (`frontend/e2e/production/`, `.github/workflows/e2e-tenant-daily.yml`, tenant setup scripts)

## Problem

The daily tenant E2E (`scripts/e2e/tenant_e2e.py`, run by `e2e-tenant-daily.yml`) is a **Python script that drives the whole flow via direct backend API calls** — submit, `uploads-complete`, `complete-review`, poll — bypassing the frontend entirely. It was built this way in June 2026 (PR #828) to prove the tenant pipeline worked, not to represent the user experience.

Two consequences:

1. **It is not a representative test.** It never exercises the tenant portal UI a real operator uses, so it cannot catch UX regressions and gives false confidence.
2. **It intermittently fails with stuck-render orphans.** Because it never loads the lyrics-review page, it never fires `POST /api/internal/encoding-worker/warmup/{job_id}` (+ `/heartbeat`) that the real review UI fires on load (`LyricsAnalyzer.tsx`). The encoding-worker VM (terminated at idle) is therefore cold when the render starts; the render worker enters its GCE-connection retry loop, and if the backend Cloud Run instance is recycled mid-retry the job orphans in `rendering_video` with no recovery. Confirmed root cause for runs #31 (vocalstar) and #32 (singa) — see the render-orphan analysis in this session's notes.

The consumer daily test (`happy-path-real-user.spec.ts`, "piri – dog") is the model done right: a real Playwright journey through the frontend that naturally fires the warmup.

## Goal

Replace the API-driven tenant script with a **Playwright frontend-journey test** that drives the tenant portal UI exactly as a real tenant operator would — which makes it representative **and** fires the warmup that prevents the orphan.

## Non-Goals (explicitly out of scope — separate follow-up)

- Deeper render-pipeline reliability hardening for real users: server-side warmup guarantee, pinning the encoding-worker URL for a render's lifetime (the undone 2026-06-15 follow-up), and a backstop recovery scheduler for jobs stuck in `rendering_video`. These protect real users from the rarer orphan and will be tracked as their own effort. This spec relies on the frontend warmup (which real tenant users already fire) to keep the happy path green.

## Design

### Approach

Replace `scripts/e2e/tenant_e2e.py` with a single parametrized Playwright spec that mirrors `happy-path-real-user.spec.ts`, run per-tenant via the existing matrix.

### 1. File layout & workflow

- **New:** `frontend/e2e/production/tenant-happy-path.spec.ts` — parametrized by `TENANT_ID` (`vocalstar` | `singa`) and `TENANT_PORTAL_URL` env vars. Structured like the consumer happy-path test.
- **Reuse helpers:** `email-testing.ts` (magic link via testmail), `auth.ts` — including `clickCompleteSignInGate()` (added earlier this session for the #870 gate), `test-cleanup.ts`.
- **Delete:** `scripts/e2e/tenant_e2e.py`.
- **Rework** `.github/workflows/e2e-tenant-daily.yml`: swap the `python scripts/e2e/tenant_e2e.py` step for `npx playwright test tenant-happy-path.spec.ts --config=playwright.production.config.ts`. Keep the `vocalstar`/`singa` matrix + `max-parallel: 1`. Add the Node/Playwright/testmail setup the consumer daily job already uses (`TESTMAIL_API_KEY`, `TESTMAIL_NAMESPACE`, `E2E_ADMIN_TOKEN`); keep WIF auth only if still needed for cleanup/GCS. Pass `TENANT_ID`/`TENANT_PORTAL_URL` per matrix entry.

### 2. The journey — entirely through the tenant portal UI

On `https://{tenant}.nomadkaraoke.com`:

1. **Sign in** via the real magic-link flow: open AuthDialog → request link to a testmail inbox → poll testmail → navigate the magic link → **Complete Sign-In gate** (`clickCompleteSignInGate`) → land in `/app`. The issued token carries `tenant_id` (verified in June).
2. **Submit track** via the tenant's simplified form: fill unique Artist/Title, upload real mixed + instrumental audio (`#tenant-mixed-audio`, `#tenant-instrumental-audio`), click *Submit Track* → assert *Track Submitted* + capture `[data-testid="created-job-id"]`.
3. **Wait for transcription** by polling the tenant portal job list UI until the *Review Lyrics* link/affordance appears.
4. **Open the review UI and approve** — the crux. Loading the review page fires `warmup` + `heartbeat`, keeping the encoder alive. Proceed through the review → instrumental step and complete it (the UI calls `completeReview(..., 'custom', ...)`; tenant instrumental is pre-supplied → `'custom'`, matching June bug-fix #7).
5. **Wait for render** by polling the UI to completion.
6. **Verify tenant distribution** (same guarantees the old script asserted, read after the UI-driven flow): Dropbox link present; **no** YouTube URL; downloads present (4K/720p/with-vocals + CDG + TXT via `job.file_urls`); job `is_private`; locked theme.
7. **Cleanup** the job (reuse `test-cleanup.ts` / admin delete).

### 3. Auth — allowlist a dedicated E2E email domain

Tenant magic-link login is gated by `tenant_config.auth.allowed_email_domains` (`users.py:287`). Current live values:
- vocalstar: `vocal-star.com`, `vocalstarmusic.com`
- singa: `singa.com`, `nomadkaraoke.com`

CI can only read **testmail.app** inboxes, which are on neither allowlist. Per the design decision, add a **dedicated E2E domain** to both tenants' `allowed_email_domains` so the test logs in exactly like a real operator.

**Domain choice (to finalize in the plan):**
- **Preferred (dedicated, owned):** a domain we control that testmail can receive for — e.g. `e2e.nomadkaraoke.com` via testmail's custom-domain feature (requires an MX record + testmail plan support). Truly dedicated; does not widen the portal to a shared public domain.
- **Fallback (simple):** `inbox.testmail.app`. Zero setup, but shared — anyone with a testmail account could register on the B2B portal. Low real risk (tenant jobs forced `is_private`, no payment on signup), but a wider surface than the owned-domain option.

Apply the change in **both** `scripts/setup-{vocalstar,singa}-tenant.py` **and** the live GCS `tenants/{id}/config.json` (the setup scripts are the source of truth; the live config must be updated to take effect).

### 4. Coverage & test data

- **Both tenants** (`vocalstar`, `singa`) via the matrix, sequential (`max-parallel: 1`) — the shared single encoder can't do two concurrent renders.
- **Real audio pair** (existing `gs://…/e2e-tests/shared/{e2e-mixed,e2e-instrumental}.mp3`, or bundled test assets). **Unique Artist/Title per run** (encoder caches by base name — repeated identical titles collide).

## Open items to resolve during planning/implementation

1. **E2E domain**: confirm dedicated-owned (`e2e.nomadkaraoke.com` via testmail custom domain — check plan/DNS) vs. fallback `inbox.testmail.app`.
2. **Review-approve selectors**: the exact tenant review/instrumental UI affordances that drive `completeReview('custom')` — nail down against the real UI during implementation.
3. **WIF vs testmail workflow setup**: confirm what the reworked workflow still needs from GCP auth (cleanup/GCS) vs. the consumer test's testmail/admin-token setup.
4. **Credits**: none needed — tenant portals do not require credits (separate financial agreement / manual invoicing per tenant). No credit-granting step in the test.

## Testing / verification

- Iterate the spec locally against prod for one tenant first (whichever the finalized E2E domain supports), driving the real portal via Playwright, before wiring the workflow.
- Confirm the warmup fires (review-page load → `warmup`/`heartbeat` 200s) and the render completes without orphaning.
- Then enable the reworked workflow and confirm a clean scheduled/dispatched run for both tenants.

## Risks

- Testmail custom-domain feasibility (for the dedicated-owned option) is unconfirmed — may fall back to `inbox.testmail.app`.
- The underlying render-orphan can still theoretically occur if the encoder is stopped mid-render despite warmup (the deeper reliability follow-up covers this); the frontend warmup makes it unlikely but not impossible. Retain a sane render timeout and clear failure output.
