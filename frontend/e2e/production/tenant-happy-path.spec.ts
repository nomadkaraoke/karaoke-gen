import { test, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';
import { createEmailHelper, isEmailTestingAvailable, EmailHelper } from '../helpers/email-testing';
import { clickCompleteSignInGate } from '../helpers/auth';

// Real test audio (mixed + instrumental), gitignored under e2e/fixtures.
// Populate with scripts/e2e/fetch_test_audio.sh (CI + local).
const MIXED_AUDIO = path.resolve(__dirname, '../fixtures/e2e-mixed.mp3');
const INSTRUMENTAL_AUDIO = path.resolve(__dirname, '../fixtures/e2e-instrumental.mp3');

/**
 * Tenant Portal Happy Path — Production E2E (frontend journey)
 *
 * Drives the full tenant operator journey through the tenant portal UI, exactly
 * as a real operator would, so it is representative of the user experience AND
 * fires the encoding-worker warmup (on review-page load) that keeps the encoder
 * alive for the render. Replaces the API-driven scripts/e2e/tenant_e2e.py.
 *
 * Parametrized per tenant via env:
 *   TENANT_ID          vocalstar | singa   (default: vocalstar)
 *   TENANT_PORTAL_URL  https://{tenant}.nomadkaraoke.com
 *
 * Requires: TESTMAIL_API_KEY, TESTMAIL_NAMESPACE, E2E_ADMIN_TOKEN.
 * The tenant must allowlist inbox.testmail.app in auth.allowed_email_domains.
 */

const TENANT_ID = process.env.TENANT_ID || 'vocalstar';
const PORTAL_URL = process.env.TENANT_PORTAL_URL || `https://${TENANT_ID}.nomadkaraoke.com`;
const API_URL = 'https://api.nomadkaraoke.com';
const ADMIN_TOKEN = process.env.E2E_ADMIN_TOKEN || process.env.KARAOKE_ADMIN_TOKEN || '';

const T = {
  action: 30_000,
  transcription: 900_000, // 15 min to reach review
  render: 1_800_000, // 30 min to complete render
  full: 3_000_000, // 50 min whole-test budget
};

test.describe(`Tenant Portal Happy Path — ${TENANT_ID}`, () => {
  test.describe.configure({ retries: 0 });

  let emailHelper: EmailHelper | null = null;
  let createdJobId: string | null = null;

  test.beforeAll(async () => {
    if (isEmailTestingAvailable()) emailHelper = await createEmailHelper();
  });

  test.afterAll(async () => {
    if (createdJobId && ADMIN_TOKEN) {
      // DELETE /api/jobs/{id} uses require_auth + ownership; admins bypass.
      await fetch(`${API_URL}/api/jobs/${createdJobId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      }).catch(() => {});
      console.log(`[${TENANT_ID}] Cleaned up job ${createdJobId}`);
    }
  });

  test('Full tenant journey: login -> submit -> review -> render -> distribution', async ({ page }) => {
    test.skip(!emailHelper, 'Email testing not configured (TESTMAIL_API_KEY/NAMESPACE)');
    test.skip(
      !fs.existsSync(MIXED_AUDIO) || !fs.existsSync(INSTRUMENTAL_AUDIO),
      'Test audio missing — run scripts/e2e/fetch_test_audio.sh',
    );
    test.setTimeout(T.full);

    // The shared prod config sets a global `x-playwright-test` header on every
    // request (playwright.production.config.ts). On the cross-origin PUT to the
    // GCS signed URL that header makes the browser's CORS preflight request an
    // extra header the bucket's CORS policy (Content-Type only) doesn't allow,
    // so the upload fails — a test-harness artifact, not a product bug (verified
    // the same PUT succeeds in a real browser). Strip it only for GCS uploads.
    await page.route('https://storage.googleapis.com/**', async (route) => {
      const headers = { ...route.request().headers() };
      delete headers['x-playwright-test'];
      await route.continue({ headers });
    });

    // =====================================================================
    // Sign in via the tenant portal magic-link UI
    // =====================================================================
    const inbox = await emailHelper!.createInbox();
    console.log(`[${TENANT_ID}] Test inbox: ${inbox.emailAddress}`);

    await page.goto(`${PORTAL_URL}/app`, { waitUntil: 'domcontentloaded', timeout: T.action });

    // Tenant landing shows a single "Sign In" button that opens the auth dialog.
    const signInTrigger = page.getByRole('button', { name: /sign in/i }).first();
    await expect(signInTrigger).toBeVisible({ timeout: T.action });
    await signInTrigger.click();

    const authDialog = page.getByRole('dialog');
    await expect(authDialog).toBeVisible({ timeout: T.action });
    await authDialog.getByPlaceholder('you@example.com').fill(inbox.emailAddress);

    const sendLinkButton = authDialog.getByRole('button', { name: /send sign-in link/i });
    await expect(sendLinkButton).toBeEnabled({ timeout: T.action });
    await sendLinkButton.click();

    await expect(page.getByText(/check your email/i)).toBeVisible({ timeout: 15_000 });
    console.log(`[${TENANT_ID}] Magic-link email requested`);

    const email = await emailHelper!.waitForEmail(inbox.id, 60_000);
    const magicLink = emailHelper!.extractMagicLink(email);
    if (!magicLink) {
      console.log('  Email body preview:', (email.body || '').substring(0, 500));
      throw new Error('Could not extract magic link from tenant sign-in email');
    }
    console.log(`[${TENANT_ID}] Magic link received`);

    await page.goto(magicLink);
    // Verify page gates behind an explicit "Complete Sign-In" click (see #870).
    await clickCompleteSignInGate(page);

    // New users (incl. first tenant login) see a credit interstitial; returning
    // users see "Successfully signed in!". Handle both, then land on /app.
    const verifyResult = await Promise.race([
      page.getByText(/successfully signed in/i).waitFor({ state: 'visible', timeout: T.action }).then(() => 'success'),
      page.getByText(/welcome to nomad karaoke/i).waitFor({ state: 'visible', timeout: T.action }).then(() => 'credits_interstitial'),
      page.getByText(/sign-in failed/i).waitFor({ state: 'visible', timeout: T.action }).then(() => 'error'),
    ]);
    if (verifyResult === 'error') {
      const detail = await page.locator('.text-muted-foreground').first().textContent().catch(() => null);
      throw new Error(`Tenant magic-link verification failed: ${detail ?? 'unknown'}`);
    }
    if (verifyResult === 'credits_interstitial') {
      const startButton = page.getByRole('button', { name: /start creating|go to dashboard|explore the app/i });
      await expect(startButton).toBeVisible({ timeout: T.action });
      await startButton.click();
    }
    await page.waitForURL(/\/app/, { timeout: T.action });

    const token = await page.evaluate(() => localStorage.getItem('karaoke_access_token'));
    expect(token, 'authenticated token present after magic-link login').toBeTruthy();
    console.log(`[${TENANT_ID}] Signed in via magic-link UI (${verifyResult})`);

    // =====================================================================
    // Submit a track through the tenant portal form
    // =====================================================================
    const uniqueTitle = `E2E ${TENANT_ID} ${new Date().toISOString().replace(/[:.]/g, '-')}`;
    await page.getByLabel('Artist').fill('Nomad E2E');
    await page.getByLabel('Title').fill(uniqueTitle);
    await page.locator('#tenant-mixed-audio').setInputFiles(MIXED_AUDIO);
    await page.locator('#tenant-instrumental-audio').setInputFiles(INSTRUMENTAL_AUDIO);
    await page.getByRole('button', { name: /submit track/i }).click();

    await expect(page.getByText(/track submitted/i)).toBeVisible({ timeout: 120_000 });
    const jobIdText = await page.locator('[data-testid="created-job-id"]').textContent();
    createdJobId = (jobIdText || '').replace(/^ID:\s*/i, '').trim() || null;
    expect(createdJobId, 'created job id captured from tenant submit').toBeTruthy();
    console.log(`[${TENANT_ID}] Submitted job ${createdJobId}: ${uniqueTitle}`);

    // =====================================================================
    // Wait for transcription, then open the review UI (fires warmup)
    // =====================================================================
    await page.goto(`${PORTAL_URL}/app`, { waitUntil: 'domcontentloaded' });
    let reviewLink = null;
    const reviewDeadline = Date.now() + T.transcription;
    while (Date.now() < reviewDeadline) {
      const link = page.getByRole('link', { name: /review lyrics/i }).first();
      // waitFor (not instant isVisible) so the client-side job-list fetch has
      // time to render each iteration; reload + retry if not ready yet.
      try {
        await link.waitFor({ state: 'visible', timeout: 15_000 });
        reviewLink = link;
        break;
      } catch {
        await page.reload({ waitUntil: 'domcontentloaded' });
      }
    }
    expect(reviewLink, 'Review Lyrics link appeared before transcription timeout').not.toBeNull();
    console.log(`[${TENANT_ID}] Job ready for review`);

    const reviewHref = await reviewLink!.getAttribute('href');
    const reviewUrl = reviewHref!.startsWith('http') ? reviewHref! : `${PORTAL_URL}${reviewHref}`;

    // The review page must fire the encoding-worker warmup on load — this is
    // what keeps the encoder alive for the render (and what the old API-driven
    // test skipped, causing stuck-render orphans).
    const warmupReq = page.waitForRequest(
      (r) => r.url().includes('/api/internal/encoding-worker/warmup/'),
      { timeout: 60_000 },
    );
    await page.goto(reviewUrl, { waitUntil: 'domcontentloaded' });
    await warmupReq;
    console.log(`[${TENANT_ID}] Review page fired encoding-worker warmup`);

    // =====================================================================
    // Approve the review through the UI (Preview -> Instrumental -> Confirm)
    // =====================================================================
    // Preview Video (bottom of the review page) — generates a preview using the
    // now-warm encoder, then opens a modal whose CTA reads "Complete Track" for
    // tenant/uploaded-instrumental jobs (no instrumental-review step); older builds
    // said "Proceed to Instrumental Review". Match either so the test is deploy-safe.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: T.action });
    await previewBtn.click();

    const modal = page.getByRole('dialog');
    await expect(modal).toBeVisible({ timeout: T.action });
    // Preview generation can take a bit; don't fail the journey if it's slow.
    await page.getByText(/generating preview video/i)
      .waitFor({ state: 'hidden', timeout: 180_000 })
      .catch(() => {});

    const proceedBtn = page.getByRole('button', { name: /complete track|proceed to instrumental/i });
    await expect(proceedBtn).toBeVisible({ timeout: T.action });
    await proceedBtn.click();

    const sessionToken = await page.evaluate(() => localStorage.getItem('karaoke_access_token'));
    const getJob = async () => {
      const r = await fetch(`${API_URL}/api/jobs/${createdJobId}`, {
        headers: { Authorization: `Bearer ${sessionToken}` },
      });
      if (!r.ok) throw new Error(`GET job ${createdJobId}: ${r.status}`);
      return r.json();
    };

    // Core tenant invariant: the operator ALWAYS uploads their own instrumental, so
    // there is no audio separation and therefore NO instrumental-selection step —
    // "Proceed to Instrumental Review" must complete the review directly (redirect to
    // /app). If the InstrumentalSelector (#submit-btn) ever renders, the tenant flow
    // has regressed (separation ran / the operator is being asked to pick a stem),
    // which is exactly the real-world scenario this test exists to protect.
    const submitBtn = page.locator('#submit-btn');
    const instrumentalStepAppeared = await submitBtn
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    expect(
      instrumentalStepAppeared,
      'tenant with a pre-supplied instrumental must NOT be shown an instrumental-selection step (no audio separation)',
    ).toBe(false);
    console.log(`[${TENANT_ID}] No instrumental-selection step (as expected for pre-supplied instrumental)`);

    // Confirm the review actually advanced (left awaiting_review) before waiting
    // on the render — otherwise a silent no-op would masquerade as a render hang.
    let left = false;
    for (let i = 0; i < 8; i++) {
      const s = (await getJob()).status;
      if (s !== 'awaiting_review' && s !== 'in_review' && s !== 'reviewing') { left = true; break; }
      await new Promise((res) => setTimeout(res, 5_000));
    }
    expect(left, 'review completed (job left awaiting_review)').toBeTruthy();
    console.log(`[${TENANT_ID}] Review approved -> render started`);

    // =====================================================================
    // Wait for the render to complete, then assert tenant distribution
    // =====================================================================

    let job: any = null;
    const renderDeadline = Date.now() + T.render;
    while (Date.now() < renderDeadline) {
      job = await getJob();
      if (job.status === 'complete') break;
      if (job.status === 'failed' || job.status === 'error') {
        throw new Error(`Render failed: status=${job.status} ${JSON.stringify(job.state_data?.error || '')}`);
      }
      await new Promise((res) => setTimeout(res, 15_000));
    }
    expect(job?.status, 'job reached complete before render timeout').toBe('complete');
    console.log(`[${TENANT_ID}] Render complete`);

    // Tenant distribution invariants (field paths per the proven tenant flow:
    // dropbox_link + youtube_url live in state_data; downloads in file_urls).
    const sd = job.state_data || {};
    const fileUrls = job.file_urls || {};
    const finals = Object.keys(fileUrls.finals || {});
    const packages = Object.keys(fileUrls.packages || {});
    expect(sd.dropbox_link || job.dropbox_link, 'Dropbox link present').toBeTruthy();
    expect(sd.youtube_url || job.youtube_url || null, 'no YouTube upload for tenant').toBeFalsy();
    for (const f of ['lossy_4k_mp4', 'lossy_720p_mp4', 'with_vocals_mp4']) expect(finals).toContain(f);
    for (const p of ['cdg_zip', 'txt_zip']) expect(packages).toContain(p);
    expect(job.is_private, 'tenant job is private').toBeTruthy();

    // Real-world tenant-scenario invariants (beyond "it completed"): these guard the
    // exact flow tenant operators rely on — they upload their own audio + instrumental,
    // so audio search / separation / instrumental review must all be bypassed.
    // 1. The operator's uploaded instrumental is what's used, not a separated stem.
    expect(sd.instrumental_selection, 'uploaded instrumental used (custom), not a separated stem').toBe('custom');
    // 2. Lyrics were transcribed + corrected — the job did real work, not a short-circuit.
    expect(sd.lyrics_complete, 'lyrics transcription completed').toBeTruthy();
    console.log(`[${TENANT_ID}] PASSED — complete, private, Dropbox present, no YouTube, uploaded instrumental used, downloads available`);
  });
});
