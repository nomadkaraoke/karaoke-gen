# Tenant Portal Frontend E2E — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the API-bypassing `scripts/e2e/tenant_e2e.py` with a Playwright frontend-journey test that drives the tenant portal UI end-to-end, so the daily tenant E2E is representative of the real operator experience and fires the encoding-worker warmup that prevents stuck-render orphans.

**Architecture:** A single parametrized Playwright spec (`tenant-happy-path.spec.ts`), run per-tenant via the existing `e2e-tenant-daily.yml` matrix, that mirrors the consumer `happy-path-real-user.spec.ts`: magic-link login on the tenant subdomain → submit track via the tenant form → wait for transcription → open the review UI (fires `warmup`/`heartbeat`) → approve → wait for render → assert tenant distribution → cleanup.

**Tech Stack:** Playwright (`@playwright/test`), TypeScript, testmail.app (E2E email), GitHub Actions matrix, FastAPI backend (tenant config in GCS).

## Global Constraints

- **Verification model:** These are production E2E tests. A task's "test cycle" is *running the spec (or the built-up portion of it) against production* and observing the segment pass — not unit tests. Use `cd frontend && ./node_modules/.bin/playwright test tenant-happy-path.spec.ts --config=playwright.production.config.ts --project=chromium --reporter=list` with the required env vars set.
- **Required env vars for local runs:** `TESTMAIL_API_KEY`, `TESTMAIL_NAMESPACE`, `E2E_ADMIN_TOKEN` (admin token, `gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d, -f1`), `TENANT_ID` (`vocalstar`|`singa`), `TENANT_PORTAL_URL` (`https://{tenant}.nomadkaraoke.com`).
- **testmail address domain is `inbox.testmail.app`** — hardcoded in `frontend/e2e/helpers/email-testing.ts:133`. The tenant allowlist decision (Task 1) is bound to this: allowlisting `inbox.testmail.app` is the minimal path; a truly-owned dedicated domain would additionally require testmail custom-domain support + a helper change (out of scope for this plan — see Task 1 note).
- **Encoder is a single shared worker** — tenants must run sequentially (`max-parallel: 1`), and every run must use a **unique Artist/Title** (encoder caches by base name; repeated titles collide).
- **Tenant distribution invariants** (assert these): Dropbox link present, **no** YouTube URL, downloads from `job.file_urls` present (finals: `lossy_4k_mp4`, `lossy_720p_mp4`, `with_vocals_mp4`; packages: `cdg_zip`, `txt_zip`), job `is_private`, locked theme.
- **No credits needed** — tenant portals do not require credits (manual invoicing per tenant). Do not add a credit-granting step.
- **Reuse existing helpers** — `createEmailHelper`/`isEmailTestingAvailable` (`email-testing.ts`), `clickCompleteSignInGate` (`auth.ts`), cleanup via admin `DELETE /api/admin/jobs/{id}` (`test-cleanup.ts` pattern). Do not reinvent them.
- **Commit style:** end commit messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Bump `pyproject.toml` version once (Task 7).

## File Structure

- **Create:** `frontend/e2e/production/tenant-happy-path.spec.ts` — the parametrized tenant journey spec. One responsibility: drive one tenant's full portal journey and assert distribution.
- **Modify:** `scripts/setup-vocalstar-tenant.py`, `scripts/setup-singa-tenant.py` — add the E2E domain to `allowed_email_domains`.
- **Create:** `scripts/e2e/update_tenant_allowlist.py` — small idempotent script to apply the `allowed_email_domains` change to the **live** GCS `tenants/{id}/config.json` (setup scripts are source-of-truth; live config must also be updated).
- **Modify:** `.github/workflows/e2e-tenant-daily.yml` — run the Playwright spec per matrix tenant; add Node/Playwright/testmail setup; keep matrix + `max-parallel: 1`.
- **Delete:** `scripts/e2e/tenant_e2e.py`.
- **Modify:** `pyproject.toml` — version bump (Task 7).

---

### Task 1: Allowlist the E2E email domain on both tenants

**Files:**
- Modify: `scripts/setup-vocalstar-tenant.py` (the `allowed_email_domains` list)
- Modify: `scripts/setup-singa-tenant.py` (the `allowed_email_domains` list)
- Create: `scripts/e2e/update_tenant_allowlist.py`

**Note (decision):** Uses `inbox.testmail.app`. The spec's "dedicated owned domain" preference (`e2e.nomadkaraoke.com`) needs testmail custom-domain support + a change to the hardcoded domain in `email-testing.ts`; that is deferred. If the shared-domain surface is later deemed unacceptable, swap the constant here and in the helper. Risk is low: tenant jobs are forced `is_private`, no payment on signup.

**Interfaces:**
- Produces: both tenants' live config `auth.allowed_email_domains` contains `inbox.testmail.app`, so `POST /api/users/auth/magic-link` with a testmail address + `X-Tenant-ID: {tenant}` returns 200 (not domain-rejected).

- [ ] **Step 1: Add the domain to the setup scripts**

In `scripts/setup-vocalstar-tenant.py`, change:
```python
            "allowed_email_domains": ["vocal-star.com", "vocalstarmusic.com"],
```
to:
```python
            "allowed_email_domains": ["vocal-star.com", "vocalstarmusic.com", "inbox.testmail.app"],
```
In `scripts/setup-singa-tenant.py`, change:
```python
            "allowed_email_domains": ["singa.com", "nomadkaraoke.com"],
```
to:
```python
            "allowed_email_domains": ["singa.com", "nomadkaraoke.com", "inbox.testmail.app"],
```

- [ ] **Step 2: Write the live-config updater script**

Create `scripts/e2e/update_tenant_allowlist.py`:
```python
#!/usr/bin/env python3
"""Idempotently add inbox.testmail.app to a tenant's allowed_email_domains in
the live GCS config (tenants/{id}/config.json). Setup scripts are the source
of truth, but the live config must be updated to take effect.

Usage: python scripts/e2e/update_tenant_allowlist.py vocalstar singa
"""
import json
import sys
from google.cloud import storage

BUCKET = "karaoke-gen-storage-nomadkaraoke"
E2E_DOMAIN = "inbox.testmail.app"


def update(tenant_id: str) -> None:
    client = storage.Client(project="nomadkaraoke")
    blob = client.bucket(BUCKET).blob(f"tenants/{tenant_id}/config.json")
    cfg = json.loads(blob.download_as_text())
    auth = cfg.setdefault("auth", {})
    domains = auth.setdefault("allowed_email_domains", [])
    if E2E_DOMAIN in domains:
        print(f"{tenant_id}: already allowlisted ({domains})")
        return
    domains.append(E2E_DOMAIN)
    blob.upload_from_string(json.dumps(cfg, indent=2), content_type="application/json")
    print(f"{tenant_id}: added {E2E_DOMAIN} -> {domains}")


if __name__ == "__main__":
    for t in sys.argv[1:] or ["vocalstar", "singa"]:
        update(t)
```

- [ ] **Step 3: Apply to live config**

Run: `cd <worktree> && python scripts/e2e/update_tenant_allowlist.py vocalstar singa`
Expected: prints `vocalstar: added inbox.testmail.app -> [...]` and same for singa. (Uses ADC — the read-only SA can read; if it lacks write, run with an admin-capable credential / break-glass per project convention. If write is blocked, STOP and notify.)

- [ ] **Step 4: Verify magic-link is accepted for a testmail address on the tenant portal**

Run:
```bash
curl -s -X POST "https://api.nomadkaraoke.com/api/users/auth/magic-link" \
  -H "Content-Type: application/json" -H "X-Tenant-ID: vocalstar" \
  -d '{"email": "'"$TESTMAIL_NAMESPACE"'.probe@inbox.testmail.app"}'
```
Expected: HTTP 200 / `{"status": ...}` (NOT a domain-rejection error). Repeat with `X-Tenant-ID: singa`.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup-vocalstar-tenant.py scripts/setup-singa-tenant.py scripts/e2e/update_tenant_allowlist.py
git commit -m "feat(e2e): allowlist testmail domain for tenant magic-link login"
```

---

### Task 2: Scaffold the spec + magic-link login segment

**Files:**
- Create: `frontend/e2e/production/tenant-happy-path.spec.ts`

**Interfaces:**
- Consumes: `createEmailHelper`, `isEmailTestingAvailable` from `../helpers/email-testing`; `clickCompleteSignInGate` from `../helpers/auth`.
- Produces: an authenticated tenant session in `page` (localStorage `karaoke_access_token` set, token carries `tenant_id`), reached entirely via the portal UI.

- [ ] **Step 1: Create the spec with env parametrization + login segment**

```typescript
import { test, expect, Page } from '@playwright/test';
import { createEmailHelper, isEmailTestingAvailable, EmailHelper } from '../helpers/email-testing';
import { clickCompleteSignInGate } from '../helpers/auth';

const TENANT_ID = process.env.TENANT_ID || 'vocalstar';
const PORTAL_URL = process.env.TENANT_PORTAL_URL || `https://${TENANT_ID}.nomadkaraoke.com`;
const API_URL = 'https://api.nomadkaraoke.com';
const ADMIN_TOKEN = process.env.E2E_ADMIN_TOKEN || process.env.KARAOKE_ADMIN_TOKEN || '';

const T = { action: 30_000, transcription: 900_000, render: 1_800_000, full: 3_000_000 };

test.describe(`Tenant Portal Happy Path — ${TENANT_ID}`, () => {
  test.describe.configure({ retries: 0 });
  let emailHelper: EmailHelper | null = null;
  let createdJobId: string | null = null;

  test.beforeAll(async () => {
    if (isEmailTestingAvailable()) emailHelper = await createEmailHelper();
  });

  test.afterAll(async () => {
    if (createdJobId && ADMIN_TOKEN) {
      await fetch(`${API_URL}/api/admin/jobs/${createdJobId}`, {
        method: 'DELETE', headers: { 'X-Admin-Token': ADMIN_TOKEN },
      }).catch(() => {});
    }
  });

  test(`Full tenant journey: login -> submit -> review -> render -> distribution`, async ({ page, context }) => {
    test.skip(!emailHelper, 'Email testing not configured (TESTMAIL_API_KEY/NAMESPACE)');
    test.setTimeout(T.full);

    // ---- Sign in via magic link on the tenant portal ----
    const inbox = await emailHelper!.createInbox();
    await page.goto(`${PORTAL_URL}/app`, { waitUntil: 'domcontentloaded', timeout: T.action });

    const signInButton = page.getByRole('button', { name: /sign (in|up)/i }).first();
    await expect(signInButton).toBeVisible({ timeout: T.action });
    await signInButton.click();

    const authDialog = page.getByRole('dialog');
    await expect(authDialog).toBeVisible({ timeout: T.action });
    await authDialog.getByPlaceholder('you@example.com').fill(inbox.emailAddress);
    await page.getByRole('button', { name: /send sign-in link/i }).click();
    await expect(page.getByText(/check your email/i)).toBeVisible({ timeout: 15_000 });

    const email = await emailHelper!.waitForEmail(inbox.id, 60_000);
    const magicLink = emailHelper!.extractMagicLink(email);
    if (!magicLink) throw new Error('Could not extract magic link from tenant sign-in email');

    await page.goto(magicLink);
    await clickCompleteSignInGate(page);
    await page.waitForURL(/\/app/, { timeout: T.action });

    const token = await page.evaluate(() => localStorage.getItem('karaoke_access_token'));
    expect(token, 'authenticated token present after magic-link login').toBeTruthy();
    console.log(`[${TENANT_ID}] Signed in via magic-link UI`);
  });
});
```

- [ ] **Step 2: Run the login segment against prod (singa first — the allowlist from Task 1 is live)**

Run: `cd frontend && ./node_modules/.bin/playwright test tenant-happy-path.spec.ts --config=playwright.production.config.ts --project=chromium --reporter=list`
(with `TENANT_ID=singa TENANT_PORTAL_URL=https://singa.nomadkaraoke.com` + testmail/admin env).
Expected: reaches `/app`, logs `Signed in via magic-link UI`, token present. (The test will continue past login only once later tasks add steps; for now it ends after the login assertion.)

- [ ] **Step 3: Confirm the token carries the tenant_id**

Add a temporary log of the decoded token payload or verify via `GET /api/users/me` with the token that the session is tenant-scoped. Expected: `tenant_id == singa`. Remove the temporary log before commit.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/production/tenant-happy-path.spec.ts
git commit -m "feat(e2e): tenant-happy-path spec scaffold + magic-link login segment"
```

---

### Task 3: Submit-track segment

**Files:**
- Modify: `frontend/e2e/production/tenant-happy-path.spec.ts`

**Interfaces:**
- Consumes: authenticated `page` from Task 2.
- Produces: `createdJobId` set to the submitted job's short id; job is transcribing.

**Test audio:** download the shared E2E pair to temp files at test start:
`gs://karaoke-gen-storage-nomadkaraoke/e2e-tests/shared/e2e-mixed.mp3` and `.../e2e-instrumental.mp3`. Fetch via the public/authorized GCS path or a signed URL helper; if not directly fetchable in CI, stage them as repo test assets under `frontend/e2e/fixtures/`. Confirm the fetch path during this task's run.

- [ ] **Step 1: Add the submit segment after login**

Insert before the closing of the test body:
```typescript
    // ---- Submit a track via the tenant form ----
    const uniqueTitle = `E2E ${TENANT_ID} ${new Date().toISOString().replace(/[:.]/g, '-')}`;
    await page.getByLabel('Artist').fill('Nomad E2E');
    await page.getByLabel('Title').fill(uniqueTitle);
    await page.locator('#tenant-mixed-audio').setInputFiles(mixedAudioPath);
    await page.locator('#tenant-instrumental-audio').setInputFiles(instrumentalAudioPath);
    await page.getByRole('button', { name: /submit track/i }).click();

    await expect(page.getByText(/track submitted/i)).toBeVisible({ timeout: 120_000 });
    const jobIdText = await page.locator('[data-testid="created-job-id"]').textContent();
    createdJobId = jobIdText?.replace(/^ID:\s*/i, '').trim() || null;
    expect(createdJobId, 'created job id captured from tenant submit').toBeTruthy();
    console.log(`[${TENANT_ID}] Submitted job ${createdJobId}: ${uniqueTitle}`);
```
Add the audio-staging code in `beforeAll` (or top of test) producing `mixedAudioPath` / `instrumentalAudioPath` (temp files). Use a unique title per run (constraint).

- [ ] **Step 2: Run login+submit against prod (singa)**

Run: same command as Task 2 Step 2.
Expected: `Track Submitted` appears, `createdJobId` logged. The `afterAll` cleanup deletes the job.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/production/tenant-happy-path.spec.ts
git commit -m "feat(e2e): tenant submit-track segment via portal form"
```

---

### Task 4: Wait-for-transcription + open-review (warmup) + approve segment

**Files:**
- Modify: `frontend/e2e/production/tenant-happy-path.spec.ts`

**Interfaces:**
- Consumes: `createdJobId`, authenticated `page`.
- Produces: job past review (status left `awaiting_review`), review-page load having fired `warmup`+`heartbeat`.

**Pattern to follow:** `happy-path-real-user.spec.ts` Steps 7–8 (poll job card for a `Review Lyrics` link → open review page in a new tab → the page fires `warmupEncodingWorker(jobId)` on load → proceed to completion). The exact tenant review→approve affordance that calls `completeReview(..., 'custom')` must be confirmed against the live tenant review UI during this task's run (this is the legitimate E2E test cycle). Opening the review page is the essential warmup-firing action.

- [ ] **Step 1: Add poll-for-review-readiness**

```typescript
    // ---- Wait until the job is ready for review (poll the portal UI) ----
    const jobCard = page.locator(`text=${createdJobId}`).locator('xpath=ancestor::*[self::div][1]');
    let reviewLink = null;
    const deadline = Date.now() + T.transcription;
    while (Date.now() < deadline) {
      await page.reload({ waitUntil: 'domcontentloaded' });
      const link = page.getByRole('link', { name: /review lyrics/i }).first();
      if (await link.isVisible().catch(() => false)) { reviewLink = link; break; }
      await page.waitForTimeout(15_000);
    }
    expect(reviewLink, 'Review Lyrics link appeared before transcription timeout').not.toBeNull();
```

- [ ] **Step 2: Open the review page (fires warmup) and capture the warmup request**

```typescript
    const reviewHref = await reviewLink!.getAttribute('href');
    const reviewUrl = reviewHref!.startsWith('http') ? reviewHref! : `${PORTAL_URL}${reviewHref}`;
    const reviewPage = await context.newPage();
    // Assert the warmup fires on review-page load (the whole point of the UI journey)
    const warmupReq = reviewPage.waitForRequest(
      (r) => r.url().includes('/api/internal/encoding-worker/warmup/'),
      { timeout: 60_000 },
    );
    await reviewPage.goto(reviewUrl, { waitUntil: 'domcontentloaded' });
    await warmupReq;
    console.log(`[${TENANT_ID}] Review page fired encoding-worker warmup`);
```

- [ ] **Step 3: Complete the review through the UI**

Follow the happy-path Step 7–8 flow adapted for the tenant (pre-supplied instrumental → `'custom'`). Confirm the exact finalize affordance against the live tenant review UI while running this task; encode the confirmed selectors here. The completion triggers `completeReview(jobId, ..., 'custom', ...)` and moves the job into the render pipeline.

- [ ] **Step 4: Run login→submit→review-approve against prod (singa)**

Run: same command.
Expected: logs the warmup firing, review completes, job leaves `awaiting_review` (verify via a `GET /api/jobs/{id}` status check with the session token or admin token).

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e/production/tenant-happy-path.spec.ts
git commit -m "feat(e2e): tenant review-open (warmup) + approve segment"
```

---

### Task 5: Wait-for-render + distribution assertions

**Files:**
- Modify: `frontend/e2e/production/tenant-happy-path.spec.ts`

**Interfaces:**
- Consumes: `createdJobId`, session token.
- Produces: full green journey for one tenant (singa).

- [ ] **Step 1: Poll to render completion, then assert tenant distribution**

```typescript
    // ---- Wait for render to complete, then assert tenant distribution ----
    const token2 = await page.evaluate(() => localStorage.getItem('karaoke_access_token'));
    const getJob = async () => {
      const r = await fetch(`${API_URL}/api/jobs/${createdJobId}`, {
        headers: { Authorization: `Bearer ${token2}` },
      });
      if (!r.ok) throw new Error(`GET job ${createdJobId}: ${r.status}`);
      return r.json();
    };

    const renderDeadline = Date.now() + T.render;
    let job: any = null;
    while (Date.now() < renderDeadline) {
      job = await getJob();
      if (job.status === 'complete') break;
      if (job.status === 'failed' || job.status === 'error') {
        throw new Error(`Render failed: status=${job.status} ${JSON.stringify(job.state_data?.error || '')}`);
      }
      await new Promise((res) => setTimeout(res, 15_000));
    }
    expect(job?.status, 'job reached complete before render timeout').toBe('complete');

    const fileUrls = job.file_urls || {};
    const finals = Object.keys(fileUrls.finals || {});
    const packages = Object.keys(fileUrls.packages || {});
    expect(job.dropbox_url || job.state_data?.dropbox_url, 'Dropbox link present').toBeTruthy();
    expect(job.youtube_url || null, 'no YouTube upload for tenant').toBeFalsy();
    for (const f of ['lossy_4k_mp4', 'lossy_720p_mp4', 'with_vocals_mp4']) expect(finals).toContain(f);
    for (const p of ['cdg_zip', 'txt_zip']) expect(packages).toContain(p);
    expect(job.is_private, 'tenant job is private').toBeTruthy();
    console.log(`[${TENANT_ID}] PASSED — complete, Dropbox present, no YouTube, downloads available`);
```
(Confirm the exact field names — `dropbox_url` vs `state_data.dropbox_url`, `is_private` location — against a real completed job's `GET /api/jobs/{id}` payload during this run; the old `tenant_e2e.py` read `file_urls` finals/packages, `dropbox_link`, `youtube_url`.)

- [ ] **Step 2: Run the FULL journey against prod (singa) end-to-end**

Run: same command, allow up to ~30–40 min.
Expected: `[singa] PASSED …`. This is the first full green frontend journey.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/production/tenant-happy-path.spec.ts
git commit -m "feat(e2e): tenant render-wait + distribution assertions (full journey green)"
```

---

### Task 6: Confirm vocalstar + robust cleanup

**Files:**
- Modify: `frontend/e2e/production/tenant-happy-path.spec.ts` (only if selector/field differences surface)

- [ ] **Step 1: Run the full journey for vocalstar**

Run: same command with `TENANT_ID=vocalstar TENANT_PORTAL_URL=https://vocalstar.nomadkaraoke.com`.
Expected: `[vocalstar] PASSED …`. Fix any tenant-specific selector/field differences inline (both tenants use the same portal app, so differences should be minimal).

- [ ] **Step 2: Verify cleanup deletes the job for both tenants**

Confirm `afterAll` deleted each test job (`GET /api/jobs/{id}` → 404, or check admin). No orphaned E2E jobs left.

- [ ] **Step 3: Commit (if any changes)**

```bash
git add frontend/e2e/production/tenant-happy-path.spec.ts
git commit -m "fix(e2e): tenant-happy-path passes for both vocalstar and singa"
```

---

### Task 7: Rework the workflow + delete the old script + version bump

**Files:**
- Modify: `.github/workflows/e2e-tenant-daily.yml`
- Delete: `scripts/e2e/tenant_e2e.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace the run step in the workflow**

Replace the Python "Run tenant E2E" step with the Playwright run, keeping the `matrix` (`vocalstar`, `singa`) and `max-parallel: 1`. Add the Node/Playwright/testmail setup that the consumer daily job (`e2e-daily.yml`) already uses. Concretely, the job's steps become:
```yaml
      - name: Checkout code
        uses: actions/checkout@v4
      - name: Setup Node.js
        uses: actions/setup-node@v4
        with: { node-version: '20', cache: 'npm', cache-dependency-path: frontend/package-lock.json }
      - name: Install dependencies
        working-directory: frontend
        run: npm ci --legacy-peer-deps
      - name: Install Playwright browsers
        working-directory: frontend
        run: npx playwright install --with-deps chromium
      - name: Ensure testmail.app domain is allowlisted (rate-limit)
        run: |
          curl -sf -X POST "https://api.nomadkaraoke.com/api/admin/rate-limits/blocklists/allowlisted-domains" \
            -H "X-Admin-Token: ${{ secrets.E2E_ADMIN_TOKEN }}" -H "Content-Type: application/json" \
            -d '{"domain": "inbox.testmail.app"}' || echo "Warning: allowlist may already exist"
      - name: Run tenant E2E (frontend journey)
        id: run_test
        working-directory: frontend
        env:
          TESTMAIL_API_KEY: ${{ secrets.TESTMAIL_API_KEY }}
          TESTMAIL_NAMESPACE: ${{ secrets.TESTMAIL_NAMESPACE }}
          E2E_ADMIN_TOKEN: ${{ secrets.E2E_ADMIN_TOKEN }}
          TENANT_ID: ${{ matrix.tenant }}
          TENANT_PORTAL_URL: https://${{ matrix.tenant }}.nomadkaraoke.com
        run: |
          npx playwright test tenant-happy-path.spec.ts \
            --config=playwright.production.config.ts --project=chromium \
            --reporter=list --timeout=2400000 2>&1 | tee e2e-output.log
          echo "exit_code=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"
        continue-on-error: true
```
Keep the existing "Upload logs", "Discord notification (failure)", "Fail the job", and `notify-email` steps; point the log upload at `frontend/e2e-output.log` and `frontend/test-results/`. Drop the GCP WIF auth + `pip install` steps unless still needed (they were for the Python script's Secret Manager/GCS access — the Playwright test uses `E2E_ADMIN_TOKEN` directly; remove if unused).

- [ ] **Step 2: Delete the old Python script**

```bash
git rm scripts/e2e/tenant_e2e.py
```

- [ ] **Step 3: Bump version**

In `pyproject.toml`, bump the patch version (e.g. `0.190.3` → `0.190.4`).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/e2e-tenant-daily.yml pyproject.toml
git commit -m "ci(e2e): run tenant daily via Playwright frontend journey; drop API script"
```

---

### Task 8: Final end-to-end verification via the workflow

- [ ] **Step 1: Dispatch the reworked workflow against the branch, off-peak if possible**

Run: `gh workflow run e2e-tenant-daily.yml --repo nomadkaraoke/karaoke-gen --ref <branch>`
(Only the default branch may be dispatchable; if so, this runs after merge. Prefer a low-traffic window given the shared encoder.)

- [ ] **Step 2: Watch both matrix jobs to green**

Confirm `Tenant E2E: vocalstar` and `Tenant E2E: singa` both succeed, and the logs show the warmup firing + render completing (no `rendering_video` orphan). If a job stalls at `rendering_video`, capture logs — that would indicate the warmup did not prevent the orphan and the separate reliability follow-up is needed.

- [ ] **Step 3: Confirm no leftover E2E jobs**

Check admin/job list for orphaned `Nomad E2E` jobs; none should remain.

---

## Self-Review

**Spec coverage:**
- Representative frontend journey (login→submit→review→render→verify) → Tasks 2–6. ✓
- Delete `tenant_e2e.py` / rework workflow → Task 7. ✓
- Allowlist dedicated E2E domain on both tenants (scripts + live config) → Task 1. ✓
- Both tenants, sequential, unique title, real audio → Tasks 3, 6, Global Constraints. ✓
- Distribution invariants → Task 5 + Global Constraints. ✓
- No credits step → Global Constraints. ✓
- Warmup-fires assertion (the crux) → Task 4 Step 2. ✓
- Non-goal (reliability hardening) → explicitly excluded; Task 8 Step 2 flags if warmup proves insufficient. ✓

**Placeholder scan:** Two spots require live-UI confirmation (Task 4 Step 3 review-approve affordance; Task 5 Step 1 exact job field names). These are inherent to E2E authoring against a live UI, not deferred implementation — each names the exact mechanism, the reference (`happy-path` Steps 7–8; old `tenant_e2e.py` fields), and the run in which to confirm. Acceptable; not fixable ahead of a live run without guessing selectors.

**Type/name consistency:** `createdJobId`, `emailHelper`, `PORTAL_URL`, `TENANT_ID`, `reviewUrl`, `getJob()` used consistently across tasks. Helper names (`createEmailHelper`, `waitForEmail`, `extractMagicLink`, `clickCompleteSignInGate`) match the real exports verified in the source.
