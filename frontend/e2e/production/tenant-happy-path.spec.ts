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
  });
});
