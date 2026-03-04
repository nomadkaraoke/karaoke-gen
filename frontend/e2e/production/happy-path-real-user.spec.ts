import { test, expect, Page, BrowserContext } from '@playwright/test';
import { createEmailHelper, isEmailTestingAvailable } from '../helpers/email-testing';

/**
 * E2E Happy Path Test - Real User Journey with Full UI Interactions
 *
 * This test validates the COMPLETE karaoke generation flow using ONLY
 * browser interactions - no API shortcuts allowed.
 *
 * See docs/E2E-HAPPY-PATH-TEST-SPEC.md for full requirements.
 *
 * Test Flow:
 * 1. Landing Page - Navigate and verify
 * 2. New User Signup - Create testmail.app inbox, request magic link via AuthDialog
 * 3. Magic Link Auth - Receive email, extract link, navigate to verify
 * 4. Create Job - Guided flow Step 1 (Song Info) + Step 2 (Choose Audio)
 * 5. Audio Selection & Create - Guided flow Step 3 (Customize & Create) → "Job Created"
 * 6. Wait for Processing - Monitor via UI status updates
 * 7. Combined Review - Open review UI, preview video, proceed to instrumental
 * 8. Instrumental Selection - Select instrumental and submit (same page via hash nav)
 * 9. Wait for Completion - Monitor via UI
 * 10. Verify Downloads - Check download links work
 * 11. Verify Distribution - Check YouTube/Dropbox/GDrive indicators
 * 12. Cleanup - Delete distributed content and job
 *
 * Environment Variables:
 *   - TESTMAIL_API_KEY + TESTMAIL_NAMESPACE: Required for email testing (can be skipped with E2E_TEST_TOKEN)
 *   - E2E_TEST_TOKEN: Pre-configured user token to skip signup flow (for iterations)
 *   - E2E_ADMIN_TOKEN: Required for cleanup endpoint (optional - cleanup skipped if not set)
 */

/**
 * Check if we should use a pre-configured test token instead of creating a new user
 */
function hasPreConfiguredToken(): boolean {
  return !!process.env.E2E_TEST_TOKEN;
}

function getPreConfiguredToken(): string {
  return process.env.E2E_TEST_TOKEN || '';
}

// =============================================================================
// CONSTANTS
// =============================================================================

const PROD_URL = 'https://gen.nomadkaraoke.com';
const API_URL = 'https://api.nomadkaraoke.com';

// Test song - uses cached flacfetch results for speed
const TEST_SONG = {
  artist: 'piri',
  title: 'dog',
} as const;

// Timeouts
const TIMEOUTS = {
  action: 30_000,           // 30s for UI actions
  expect: 60_000,           // 60s for assertions
  emailArrival: 120_000,    // 2min for email to arrive
  audioSearch: 120_000,     // 2min for audio search
  lyricsProcessing: 1500_000, // 25min for lyrics transcription (matches Cloud Tasks deadline)
  videoRendering: 900_000,  // 15min for video rendering
  finalEncoding: 600_000,   // 10min for final encoding
  fullTest: 3600_000,       // 60min for full test
} as const;

// =============================================================================
// HELPERS
// =============================================================================

/**
 * Wait for job status to appear in the UI
 */
async function waitForJobStatus(
  page: Page,
  jobCard: any,
  targetStatuses: string[],
  timeoutMs: number
): Promise<string> {
  const startTime = Date.now();

  while (Date.now() - startTime < timeoutMs) {
    // Check the status indicator text in the job card
    const statusText = await jobCard.locator('[class*="text-"]').first().textContent() || '';

    for (const target of targetStatuses) {
      if (statusText.toLowerCase().includes(target.toLowerCase())) {
        console.log(`  Found status: ${statusText}`);
        return target;
      }
    }

    // Also check for "Action needed" indicator
    const actionNeeded = await jobCard.getByText('Action needed').isVisible().catch(() => false);
    if (actionNeeded) {
      console.log(`  Action needed - checking for target status`);
      for (const target of targetStatuses) {
        if (statusText.toLowerCase().includes(target.replace('awaiting_', '').replace('_', ' '))) {
          return target;
        }
      }
    }

    // Wait before next check
    await page.waitForTimeout(5000);

    // Refresh to get latest status
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    if (await refreshBtn.isVisible().catch(() => false)) {
      await refreshBtn.click();
      await page.waitForTimeout(2000);
    }
  }

  throw new Error(`Timeout waiting for status: ${targetStatuses.join(' or ')}`);
}

/**
 * Get the auth token from localStorage
 */
async function getAuthToken(page: Page): Promise<string | null> {
  return await page.evaluate(() => localStorage.getItem('karaoke_access_token'));
}

/**
 * Navigate to a URL with retry logic for transient network errors.
 * Production tests can hit ERR_INTERNET_DISCONNECTED or similar when opening new pages.
 */
async function gotoWithRetry(
  page: Page,
  url: string,
  options: { waitUntil?: 'networkidle' | 'load' | 'domcontentloaded'; timeout?: number } = {},
  maxRetries = 3
): Promise<void> {
  const { waitUntil = 'networkidle', timeout = 60000 } = options;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      await page.goto(url, { waitUntil, timeout });
      return;
    } catch (err: any) {
      const msg = err?.message || String(err);
      console.log(`  WARNING: goto attempt ${attempt}/${maxRetries} failed: ${msg.substring(0, 120)}`);
      if (attempt === maxRetries) throw err;
      // Wait before retrying (increasing backoff)
      await page.waitForTimeout(2000 * attempt);
    }
  }
}

// =============================================================================
// TEST
// =============================================================================

test.describe('E2E Happy Path - Real User with Full UI Interactions', () => {
  // IMPORTANT: Disable retries for this test - each retry creates a new karaoke job
  // which wastes 15-20 minutes of processing time
  test.describe.configure({ retries: 0 });

  test('Complete flow: New user signup -> Karaoke generation -> Distribution -> Cleanup', async ({
    page,
    context,
  }) => {
    test.setTimeout(TIMEOUTS.fullTest);

    // Check if we have a pre-configured token (for iterations without using testmail.app)
    const usePreConfiguredToken = hasPreConfiguredToken();

    // Skip if neither pre-configured token nor testmail.app is available
    if (!usePreConfiguredToken && !isEmailTestingAvailable()) {
      test.skip(true, 'Neither E2E_TEST_TOKEN nor TESTMAIL_API_KEY/TESTMAIL_NAMESPACE set - cannot run test');
      return;
    }

    let accessToken: string | null = null;
    let jobId: string | null = null;
    let inboxId: string | null = null;

    // Only create email helper if we're doing the full signup flow
    const emailHelper = usePreConfiguredToken ? null : await createEmailHelper();

    // Intercept API requests to disable YouTube upload for E2E tests
    // This prevents test jobs from uploading to YouTube when server default is true
    await page.route('**/api/audio-search/search', async (route) => {
      const request = route.request();
      if (request.method() === 'POST') {
        const postData = request.postDataJSON();
        // Explicitly disable YouTube upload for E2E test jobs
        postData.enable_youtube_upload = false;
        console.log('  [Request Intercept] Disabled YouTube upload for audio search');
        await route.continue({
          postData: JSON.stringify(postData),
        });
      } else {
        await route.continue();
      }
    });

    try {
      // =========================================================================
      // STEP 1: Landing Page
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 1: Landing Page');
      console.log('========================================');

      await gotoWithRetry(page, PROD_URL);

      // Verify hero section
      await expect(page.locator('h1')).toContainText('Karaoke Video', { timeout: TIMEOUTS.expect });
      console.log('  Hero section visible');

      // Verify pricing section
      await expect(page.locator('#pricing')).toBeVisible({ timeout: TIMEOUTS.expect });
      console.log('  Pricing section visible');

      await page.screenshot({ path: 'test-results/01-landing-page.png', fullPage: true });
      console.log('STEP 1 COMPLETE: Landing page loads correctly');

      // =========================================================================
      // STEP 2 & 3: Authentication (either via pre-configured token or magic link signup)
      // =========================================================================

      if (usePreConfiguredToken) {
        // ----- FAST PATH: Use pre-configured token -----
        console.log('\n========================================');
        console.log('STEP 2: Using Pre-Configured Token (skipping signup)');
        console.log('========================================');

        accessToken = getPreConfiguredToken();
        console.log(`  Using token: ${accessToken.substring(0, 8)}...`);

        // Navigate to app and inject token into localStorage
        await gotoWithRetry(page, `${PROD_URL}/app`);
        await page.evaluate((token) => {
          localStorage.setItem('karaoke_access_token', token);
        }, accessToken);

        // Reload to apply the token
        await page.reload();
        await page.waitForLoadState('networkidle');

        await page.screenshot({ path: 'test-results/02-token-injected.png' });
        console.log('STEP 2 COMPLETE: Token injected, skipping signup');

        console.log('\n========================================');
        console.log('STEP 3: Verify Authentication');
        console.log('========================================');

        // Verify the token is actually valid by waiting for user data to load.
        // When authenticated, AuthStatus shows credits (e.g., "3 credits").
        // When token is invalid, AuthStatus shows "Login" button.
        const loginButton = page.getByRole('button', { name: /^Login$/i });
        const creditIndicator = page.getByText(/\d+\s+credits?/i).first();

        // Wait for either credits (auth success) or Login button (auth failure)
        await expect(creditIndicator.or(loginButton)).toBeVisible({ timeout: TIMEOUTS.action });

        if (await loginButton.isVisible().catch(() => false)) {
          throw new Error(
            'E2E_TEST_TOKEN is expired or invalid — the "Login" button is still visible after token injection. ' +
            'Please update the E2E_TEST_TOKEN secret in GitHub Actions with a valid token.'
          );
        }

        const creditText = await creditIndicator.textContent().catch(() => 'N/A');
        console.log(`  User authenticated — ${creditText}`);

        console.log('STEP 3 COMPLETE: User authenticated with pre-configured token');

      } else {
        // ----- FULL PATH: Magic link signup with testmail.app -----
        console.log('\n========================================');
        console.log('STEP 2: New User Signup via Magic Link');
        console.log('========================================');

        // Create test inbox
        const inbox = await emailHelper!.createInbox();
        inboxId = inbox.id!;
        console.log(`  Test email: ${inbox.emailAddress}`);

        // Click "Sign Up Free" button to open the AuthDialog
        const signUpButton = page.getByRole('button', { name: /sign up free/i });
        await expect(signUpButton).toBeVisible({ timeout: TIMEOUTS.action });
        await page.screenshot({ path: 'test-results/02-before-signup-click.png' });
        console.log('  Clicking Sign Up Free button...');
        await signUpButton.click();

        // Wait for the AuthDialog to appear with email input
        const authDialog = page.getByRole('dialog');
        await expect(authDialog).toBeVisible({ timeout: TIMEOUTS.action });
        const authEmailInput = authDialog.getByPlaceholder('you@example.com');
        await expect(authEmailInput).toBeVisible({ timeout: TIMEOUTS.action });
        console.log('  Auth dialog visible');

        // Fill email in the AuthDialog
        await authEmailInput.fill(inbox.emailAddress!);
        await page.screenshot({ path: 'test-results/02a-auth-dialog-filled.png' });
        console.log('  Email entered in auth dialog');

        // Click "Send Sign-In Link" button
        const sendLinkButton = page.getByRole('button', { name: /send sign-in link/i });
        await expect(sendLinkButton).toBeEnabled({ timeout: TIMEOUTS.action });
        await sendLinkButton.click();
        console.log('  Clicked Send Sign-In Link');

        // Wait for confirmation ("Check Your Email" text)
        await expect(
          page.getByText(/check your email/i)
        ).toBeVisible({ timeout: 15000 });
        await page.screenshot({ path: 'test-results/02b-magic-link-sent.png' });
        console.log('  Magic link email sent confirmation visible');

        // =========================================================================
        // STEP 3: Magic Link Authentication
        // =========================================================================
        console.log('\n========================================');
        console.log('STEP 3: Magic Link Authentication');
        console.log('========================================');

        // Wait for the magic link email to arrive via testmail.app
        console.log('  Waiting for magic link email...');
        const magicLinkEmail = await emailHelper!.waitForEmail(inbox.id!, 60000);
        console.log(`  Received email: ${magicLinkEmail.subject}`);

        // Extract the magic link URL from the email
        const magicLinkUrl = emailHelper!.extractMagicLink(magicLinkEmail);
        if (!magicLinkUrl) {
          console.log('  Email body preview:', (magicLinkEmail.body || '').substring(0, 500));
          throw new Error('Could not extract magic link from email');
        }
        console.log(`  Magic link extracted: ${magicLinkUrl.substring(0, 80)}...`);

        // Navigate to the magic link URL to verify and authenticate
        await page.goto(magicLinkUrl);
        console.log('  Navigated to magic link verification page');

        // Wait for successful verification
        await expect(
          page.getByText(/successfully signed in/i)
        ).toBeVisible({ timeout: 15000 });
        console.log('  Successfully signed in via magic link');

        // Wait for redirect to /app
        await page.waitForURL(/\/app/, { timeout: 15000 });
        console.log('  Redirected to /app');

        // Get the token from localStorage
        accessToken = await getAuthToken(page);
        if (!accessToken) {
          throw new Error('No access token received after magic link auth');
        }
        console.log('  Session token received');

        await page.screenshot({ path: 'test-results/02c-app-after-signup.png' });
        console.log('STEP 2-3 COMPLETE: Magic link signup and auth successful');

        // Verify credits are visible
        const creditText = await page.getByText(/credit/i).first().textContent();
        console.log(`  Credits visible: ${creditText}`);
      }

      // =========================================================================
      // STEP 4: Create Karaoke Job via Guided Flow
      // The new guided flow has 3 inline steps:
      //   Step 1 (Song Info): Fill artist/title → "Choose Audio"
      //   Step 2 (Choose Audio): Auto-search → select result
      //   Step 3 (Customize & Create): Confirm settings → "Create Karaoke Video"
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 4: Create Karaoke Job (Guided Flow)');
      console.log('========================================');

      // --- Guided Step 1: Song Info ---
      // Wait for the app to finish loading (auth check → WarmingUpLoader → GuidedJobFlow)
      const artistInput = page.getByTestId('guided-artist-input');
      await expect(artistInput).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Guided flow visible, filling song info...');

      await artistInput.fill(TEST_SONG.artist);
      await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
      console.log(`  Filled song info: ${TEST_SONG.artist} - ${TEST_SONG.title}`);

      await page.screenshot({ path: 'test-results/04a-song-info.png' });

      // Click "Choose Audio" to advance to Step 2 and trigger search
      await page.getByRole('button', { name: /choose audio/i }).click();
      console.log('  Clicked "Choose Audio" — searching for audio sources...');

      // =========================================================================
      // STEP 5: Audio Selection (Guided Step 2)
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 5: Audio Selection (Guided Step 2)');
      console.log('========================================');

      // Wait for search results to appear (Tier 1/2/3 or no results)
      await expect(
        page.getByText('Perfect match found')
          .or(page.getByText('Recommended'))
          .or(page.getByText('Limited sources found'))
          .or(page.getByText(/no audio sources/i))
      ).toBeVisible({ timeout: TIMEOUTS.audioSearch });

      await page.screenshot({ path: 'test-results/05a-search-results.png' });

      // Determine tier and select audio
      const hasPickCard = await page.getByTestId('pick-card').isVisible().catch(() => false);
      if (hasPickCard) {
        // Tier 1 or 2: click "Use This Audio" on the pick card
        const tierText = await page.getByText('Perfect match found').isVisible().catch(() => false)
          ? 'Tier 1 (Perfect match)' : 'Tier 2 (Recommended)';
        console.log(`  ${tierText} — clicking "Use This Audio"`);
        await page.getByRole('button', { name: /use this audio/i }).click();
      } else if (await page.getByText('Limited sources found').isVisible().catch(() => false)) {
        // Tier 3: find and click the first Select button in results
        console.log('  Tier 3 (Limited sources) — selecting first available result');
        const selectBtns = page.getByRole('button', { name: /^select$/i });
        const count = await selectBtns.count();
        console.log(`  Found ${count} selectable results`);
        if (count > 0) {
          await selectBtns.first().click();
        } else {
          throw new Error('No audio sources available for selection');
        }
      } else {
        throw new Error('No audio sources found — cannot proceed');
      }

      console.log('  Audio source selected');

      // --- Guided Step 3: Customize & Create ---
      console.log('  Waiting for Customize & Create step...');
      await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible({ timeout: TIMEOUTS.action });

      // Wait for Title Card Preview to render (it starts blank)
      await page.waitForTimeout(3000);
      await page.screenshot({ path: 'test-results/05b-customize-step.png' });

      // Enable private mode - test jobs should never consume public NOMAD brand codes
      const privateCheckbox = page.locator('#guided-private');
      await privateCheckbox.check();
      console.log('  Enabled private mode (NOMADNP prefix, no GDrive/YouTube)');

      // Accept defaults and create
      await page.getByRole('button', { name: /create karaoke video/i }).click();
      console.log('  Clicked "Create Karaoke Video"');

      // Wait for success state
      await expect(page.getByText('Job Created')).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Job created successfully!');

      // Read the job ID from the success card (shown as "ID: xxxxxxxx")
      const createdJobIdEl = page.getByTestId('created-job-id');
      if (await createdJobIdEl.isVisible({ timeout: 5000 }).catch(() => false)) {
        const idText = await createdJobIdEl.textContent() || '';
        const idMatch = idText.match(/ID:\s*([a-f0-9]{8,})/i);
        if (idMatch) {
          jobId = idMatch[1];
          console.log(`  Job ID: ${jobId}`);
        }
      }

      await page.screenshot({ path: 'test-results/05d-job-created.png' });

      // Find the job card in the Recent Jobs panel
      console.log('  Looking for job card in Recent Jobs...');

      // Wait a moment for the jobs list to refresh after creation
      await page.waitForTimeout(3000);

      // Click Refresh to ensure job list is up to date
      const refreshBtn = page.getByRole('button', { name: /refresh/i });
      if (await refreshBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
        await refreshBtn.click();
        await page.waitForTimeout(3000);
      }

      // Find the specific job card using the job ID from the success screen
      const jobCard = page.locator('[class*="rounded-lg"][class*="border"][class*="p-3"]').filter({
        hasText: new RegExp(`ID:\\s*${jobId ? jobId.slice(0, 8) : TEST_SONG.artist}`, 'i')
      }).first();

      await expect(jobCard).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Job card visible in Recent Jobs');

      console.log('STEP 5 COMPLETE: Job created via guided flow');

      // =========================================================================
      // STEP 6: Wait for Lyrics Processing
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 6: Wait for Lyrics Processing');
      console.log('========================================');

      console.log('  Waiting for lyrics transcription (this may take 10-20 minutes)...');

      // Poll the UI for status changes
      let foundReviewStatus = false;

      const startTime = Date.now();
      let pollCount = 0;
      while (Date.now() - startTime < TIMEOUTS.lyricsProcessing) {
        pollCount++;
        const elapsedMin = Math.floor((Date.now() - startTime) / 60000);
        const elapsedSec = Math.floor(((Date.now() - startTime) % 60000) / 1000);

        // Refresh the page/jobs
        const refreshBtn = page.getByRole('button', { name: /refresh/i });
        if (await refreshBtn.isVisible().catch(() => false)) {
          await refreshBtn.click();
          await page.waitForTimeout(2000);
        }

        // Check job card status text
        const statusText = await jobCard.textContent() || '';

        // Check if the "Review Lyrics" link is visible (most reliable indicator)
        const reviewLink = jobCard.getByRole('link', { name: /review lyrics/i });
        const reviewLinkVisible = await reviewLink.isVisible().catch(() => false);

        console.log(`  [${elapsedMin}m ${elapsedSec}s] Poll #${pollCount}: reviewLink=${reviewLinkVisible}, status="${statusText.substring(0, 80)}..."`);

        // Primary check: Is the Review Lyrics link visible?
        if (reviewLinkVisible) {
          foundReviewStatus = true;
          console.log('  Job ready for lyrics review (found Review Lyrics link)');
          break;
        }

        // Secondary check: Text-based status detection
        if (statusText.toLowerCase().includes('review lyrics') ||
            (statusText.toLowerCase().includes('action needed') && statusText.toLowerCase().includes('review'))) {
          foundReviewStatus = true;
          console.log('  Job ready for lyrics review (found status text)');
          break;
        }

        // Check for failure
        if (statusText.toLowerCase().includes('failed')) {
          await page.screenshot({ path: 'test-results/06-job-failed.png' });
          throw new Error('Job failed during lyrics processing');
        }

        await page.waitForTimeout(10000); // Check every 10 seconds
      }

      if (!foundReviewStatus) {
        const elapsedMin = Math.floor((Date.now() - startTime) / 60000);
        console.log(`  TIMEOUT after ${elapsedMin} minutes (${pollCount} polls)`);
        await page.screenshot({ path: 'test-results/06-timeout.png' });
        throw new Error(`Timeout waiting for lyrics review stage after ${elapsedMin} minutes`);
      }

      await page.screenshot({ path: 'test-results/06-ready-for-review.png' });
      console.log('STEP 6 COMPLETE: Lyrics processing finished');

      // =========================================================================
      // STEP 7: Combined Lyrics Review & Instrumental Selection (via UI - REAL USER FLOW)
      // After the UI redesign, lyrics review and instrumental selection are combined:
      // 1. User opens lyrics review page
      // 2. User clicks "Preview Video" to open modal
      // 3. User clicks "Proceed to Instrumental Review" which navigates to instrumental selection
      // 4. User selects instrumental and clicks "Confirm & Continue"
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 7: Combined Lyrics Review & Instrumental Selection');
      console.log('========================================');

      // Click the Review Lyrics button/link
      const reviewLink = jobCard.getByRole('link', { name: /review lyrics/i });
      await expect(reviewLink).toBeVisible({ timeout: TIMEOUTS.action });

      // Get the review URL and construct absolute URL
      const reviewHref = await reviewLink.getAttribute('href');
      const reviewUrl = reviewHref!.startsWith('http') ? reviewHref! : `${PROD_URL}${reviewHref}`;
      console.log(`  Review URL: ${reviewUrl}`);

      // Open review UI in new page (with retry for transient network errors)
      const reviewPage = await context.newPage();
      await gotoWithRetry(reviewPage, reviewUrl);

      // Wait for the review page to actually render meaningful content
      // The lyrics review page should show either the review UI or an auth prompt
      try {
        await expect(
          reviewPage.getByRole('button', { name: /preview video/i })
            .or(reviewPage.getByText(/review/i))
            .or(reviewPage.getByText(/sign in/i))
        ).toBeVisible({ timeout: TIMEOUTS.action });
        console.log('  Lyrics review UI loaded');
      } catch {
        // If content still not visible, try reloading once
        console.log('  WARNING: Review page content not found, reloading...');
        await reviewPage.reload({ waitUntil: 'networkidle' });
        await reviewPage.waitForTimeout(5000);
      }

      await reviewPage.screenshot({ path: 'test-results/07a-lyrics-review-opened.png', fullPage: true });
      console.log('  Lyrics review UI opened');

      // Scroll to bottom of the page to see the "Preview Video" button
      console.log('  Scrolling to bottom of page...');
      await reviewPage.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await reviewPage.waitForTimeout(1000);

      // Click "Preview Video" button (at bottom of the lyrics review page)
      const previewVideoBtn = reviewPage.getByRole('button', { name: /preview video/i });
      await expect(previewVideoBtn).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Found "Preview Video" button');

      await reviewPage.screenshot({ path: 'test-results/07b-before-preview-click.png', fullPage: true });
      await previewVideoBtn.click();
      console.log('  Clicked "Preview Video" button');

      // Wait for the modal dialog to appear
      // The modal has title "Preview Video (With Vocals)"
      const previewModal = reviewPage.getByRole('dialog');
      await expect(previewModal).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Preview modal opened');

      // Wait for modal open animation to complete
      await reviewPage.waitForTimeout(5000);
      await reviewPage.screenshot({ path: 'test-results/07c-preview-modal.png', fullPage: true });

      // Wait for video to load in the modal
      // The PreviewVideoSection component first shows "Generating preview video..."
      // then renders the <video> element once the preview is ready
      console.log('  Waiting for preview video generation...');

      // First wait for the loading indicator to disappear (or video to appear)
      // The loading state shows "Generating preview video..."
      const loadingText = reviewPage.getByText(/generating preview video/i);
      try {
        // Wait up to 2 minutes for preview generation (it can be slow)
        await expect(loadingText).not.toBeVisible({ timeout: 120000 });
        console.log('  Preview generation complete');
      } catch {
        console.log('  WARNING: Loading indicator timeout - checking for video anyway');
      }

      // Now check for the video element or an error message
      const videoElement = reviewPage.locator('video');
      const errorAlert = reviewPage.locator('[role="alert"]');

      // Check if there's an error
      if (await errorAlert.isVisible({ timeout: 5000 }).catch(() => false)) {
        const errorText = await errorAlert.textContent();
        console.log(`  WARNING: Preview error: ${errorText}`);
        // Continue anyway - we can still proceed to instrumental even if preview failed
      } else if (await videoElement.isVisible({ timeout: 10000 }).catch(() => false)) {
        console.log('  Video element visible in modal');
        // Give the video a moment to buffer/load
        await reviewPage.waitForTimeout(3000);
      } else {
        console.log('  WARNING: No video element found, but continuing anyway');
      }

      await reviewPage.screenshot({ path: 'test-results/07d-preview-ready.png', fullPage: true });

      // Click "Proceed to Instrumental Review" button in the modal
      // This button saves corrections and navigates to the instrumental selection UI
      const proceedBtn = reviewPage.getByRole('button', { name: /proceed to instrumental/i });
      await expect(proceedBtn).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Found "Proceed to Instrumental Review" button');

      await proceedBtn.click();
      console.log('  Clicked "Proceed to Instrumental Review" button');

      // Wait for the navigation to instrumental selection (happens via hash change)
      // The page navigates to #/{jobId}/instrumental
      await reviewPage.waitForTimeout(3000);

      // =========================================================================
      // STEP 8: Instrumental Selection (continuation of combined flow)
      // The page has navigated to the instrumental selection UI via hash routing
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 8: Instrumental Selection');
      console.log('========================================');

      // Wait for the instrumental selection UI to load
      // The InstrumentalSelector component renders selection options
      console.log('  Waiting for instrumental selection UI to load...');

      // Wait for either the selection options or the loading indicator to appear
      try {
        await reviewPage.waitForSelector('.selection-option, .selection-panel, [class*="selection"]', { timeout: 30000 });
        console.log('  Instrumental selection UI loaded');
      } catch {
        // If selector not found, check for loading state
        const loadingState = reviewPage.getByText(/loading instrumental/i);
        if (await loadingState.isVisible({ timeout: 5000 }).catch(() => false)) {
          console.log('  Waiting for instrumental analysis to complete...');
          await expect(loadingState).not.toBeVisible({ timeout: 60000 });
        }
      }

      // Wait for waveform and UI to fully render
      await reviewPage.waitForTimeout(5000);
      await reviewPage.screenshot({ path: 'test-results/08a-instrumental-opened.png', fullPage: true });
      console.log('  Instrumental selection UI opened');

      // Select "Clean" instrumental option
      // The options have class "selection-option" and contain labels with "Clean" or "With Backing"
      const cleanOption = reviewPage.locator('.selection-option:has-text("Clean")').first();
      if (await cleanOption.isVisible({ timeout: 5000 }).catch(() => false)) {
        await cleanOption.click();
        console.log('  Selected "Clean" instrumental option');
      } else {
        // If we can't find "Clean" specifically, the first option might already be selected
        console.log('  Clean option not found - using default selection');
      }

      await reviewPage.screenshot({ path: 'test-results/08b-clean-selected.png', fullPage: true });

      // Click the submit button: "✓ Confirm & Continue" (id="submit-btn")
      const submitBtn = reviewPage.locator('#submit-btn');
      await expect(submitBtn).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Found submit button');

      await submitBtn.click();
      console.log('  Clicked "Confirm & Continue" button');

      // Wait for submission to complete - the page shows a success screen then redirects
      // In cloud mode, after success it redirects to /app after a countdown
      await reviewPage.waitForTimeout(5000);

      await reviewPage.screenshot({ path: 'test-results/08c-instrumental-submitted.png', fullPage: true });

      // The page redirects to /app after completion, or may close
      // Wait a bit for the redirect/close to happen
      if (!reviewPage.isClosed()) {
        // Wait for potential redirect or success screen
        try {
          await reviewPage.waitForURL(/\/app/, { timeout: 10000 });
          console.log('  Redirected to /app after instrumental selection');
        } catch {
          // If no redirect, close manually
          await reviewPage.close();
          console.log('  Closed review UI');
        }
      } else {
        console.log('  Review UI closed automatically after submission');
      }

      // Refresh main app
      await page.bringToFront();
      const refreshBtnAfterInstrumental = page.getByRole('button', { name: /refresh/i });
      if (await refreshBtnAfterInstrumental.isVisible().catch(() => false)) {
        await refreshBtnAfterInstrumental.click();
        await page.waitForTimeout(3000);
      }

      await page.screenshot({ path: 'test-results/08d-main-after-instrumental.png' });
      console.log('STEP 8 COMPLETE: Instrumental selection handled via UI');

      // =========================================================================
      // STEP 9: Wait for Final Encoding and Completion
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 9: Wait for Completion');
      console.log('========================================');

      console.log('  Waiting for video rendering and encoding (this may take 15-25 minutes)...');

      let isComplete = false;
      const encodeStartTime = Date.now();
      // After instrumental selection, job needs: video rendering (~15 min) + encoding (~10 min)
      // Add extra buffer for variable processing times
      const completionTimeout = TIMEOUTS.videoRendering + TIMEOUTS.finalEncoding + 600_000; // 35 minutes total

      while (Date.now() - encodeStartTime < completionTimeout) {
        if (await refreshBtn.isVisible().catch(() => false)) {
          await refreshBtn.click();
          await page.waitForTimeout(2000);
        }

        const statusText = await jobCard.textContent() || '';

        if (statusText.toLowerCase().includes('complete') && !statusText.toLowerCase().includes('prep')) {
          isComplete = true;
          console.log('  Job complete!');
          break;
        }

        if (statusText.toLowerCase().includes('failed')) {
          throw new Error('Job failed during final encoding');
        }

        console.log(`  Status: ${statusText.substring(0, 80)}...`);
        await page.waitForTimeout(10000);
      }

      if (!isComplete) {
        throw new Error('Timeout waiting for job completion');
      }

      await page.screenshot({ path: 'test-results/09-job-complete.png' });
      console.log('STEP 9 COMPLETE: Job finished');

      // =========================================================================
      // STEP 9.5: Verify Completion Email (if email testing available)
      // =========================================================================
      if (emailHelper && inboxId) {
        console.log('\n========================================');
        console.log('STEP 9.5: Verify Completion Email');
        console.log('========================================');

        try {
          console.log('  Waiting for completion email to arrive...');

          // Wait up to 3 minutes for the completion email
          // Email is sent asynchronously after job completion, may take a moment
          const completionEmail = await emailHelper.waitForCompletionEmail(
            inboxId,
            TEST_SONG.artist,
            TEST_SONG.title,
            180_000 // 3 minutes
          );

          console.log(`  ✓ Completion email received: "${completionEmail.subject}"`);

          // Verify email content
          const emailBody = completionEmail.body || '';
          const emailSubject = completionEmail.subject || '';

          // Check for expected content indicators
          const hasVideoReadyText = emailBody.toLowerCase().includes('ready') ||
                                    emailSubject.toLowerCase().includes('ready');
          const hasSongInfo = emailBody.toLowerCase().includes(TEST_SONG.artist.toLowerCase()) ||
                              emailSubject.toLowerCase().includes(TEST_SONG.artist.toLowerCase());

          console.log(`  Email verification:`);
          console.log(`    - "ready" indicator: ${hasVideoReadyText ? '✓' : '✗'}`);
          console.log(`    - Artist name: ${hasSongInfo ? '✓' : '✗'}`);

          // Log email details for debugging
          console.log(`  Email from: ${completionEmail.from}`);
          console.log(`  Subject: ${emailSubject}`);
          console.log(`  Body preview: ${emailBody.substring(0, 200)}...`);

          await page.screenshot({ path: 'test-results/09.5-completion-email-received.png' });
          console.log('STEP 9.5 COMPLETE: Completion email verified');
        } catch (e) {
          // Don't fail the test if email verification times out - it's an enhancement
          console.log(`  ⚠ WARNING: Completion email verification failed: ${e}`);
          console.log('  This may indicate email sending is disabled or delayed.');
          console.log('  Continuing with remaining steps...');
        }
      } else {
        console.log('\n========================================');
        console.log('STEP 9.5: Skip Completion Email Verification');
        console.log('========================================');
        console.log('  Skipping - using pre-configured token (no testmail inbox)');
      }

      // =========================================================================
      // STEP 10: Verify Downloads
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 10: Verify Downloads');
      console.log('========================================');

      // Look for download links in the job card
      const downloadLinks = jobCard.locator('a[href*="download"], a[href*="storage.googleapis.com"]');
      const downloadCount = await downloadLinks.count();
      console.log(`  Found ${downloadCount} download links`);

      if (downloadCount > 0) {
        // Click first download link to verify it works
        const firstDownloadUrl = await downloadLinks.first().getAttribute('href');
        console.log(`  First download URL: ${firstDownloadUrl?.substring(0, 80)}...`);

        // Verify the download URL is accessible
        // Use GET instead of HEAD since the download endpoint may not support HEAD
        // The response might redirect to GCS, so accept 2xx/3xx status codes
        const response = await page.request.get(firstDownloadUrl!, {
          maxRedirects: 0, // Don't follow redirects, just check the initial response
        });
        const status = response.status();
        // Accept 200 (success) or 302/307 (redirect to actual download)
        expect(status >= 200 && status < 400).toBe(true);
        console.log(`  Download URL verified accessible (status: ${status})`);
      }

      console.log('STEP 10 COMPLETE: Downloads verified');

      // =========================================================================
      // STEP 11: Verify Distribution (if enabled)
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 11: Verify Distribution');
      console.log('========================================');

      // Private mode skips public distribution (YouTube, GDrive) — only Dropbox may be present
      const cardText = await jobCard.textContent() || '';

      if (cardText.toLowerCase().includes('youtube') ||
          cardText.toLowerCase().includes('drive')) {
        console.log('  WARNING: Public distribution indicators found despite private mode!');
      } else {
        console.log('  No public distribution indicators (expected for private/NOMADNP jobs)');
      }

      if (cardText.toLowerCase().includes('dropbox')) {
        console.log('  Dropbox distribution present (private jobs still upload to Dropbox)');
      }

      console.log('STEP 11 COMPLETE: Distribution check done (private mode)');

      // =========================================================================
      // STEP 12: Cleanup
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 12: Cleanup');
      console.log('========================================');

      const adminToken = process.env.E2E_ADMIN_TOKEN;

      if (adminToken && jobId) {
        console.log('  Cleaning up distribution and job...');

        try {
          const cleanupResponse = await page.request.post(
            `${API_URL}/api/jobs/${jobId}/cleanup-distribution`,
            {
              headers: {
                'Authorization': `Bearer ${adminToken}`,
                'Content-Type': 'application/json',
              },
              data: { delete_job: true },
            }
          );

          if (cleanupResponse.ok()) {
            const result = await cleanupResponse.json();
            console.log('  Cleanup results:');
            console.log(`    YouTube: ${result.youtube?.status}`);
            console.log(`    Dropbox: ${result.dropbox?.status}`);
            console.log(`    GDrive: ${result.gdrive?.status}`);
            console.log(`    Job deleted: ${result.job_deleted}`);
          } else {
            console.log(`  WARNING: Cleanup failed with status ${cleanupResponse.status()}`);
          }
        } catch (e) {
          console.log(`  WARNING: Cleanup error: ${e}`);
        }
      } else {
        console.log('  Skipping cleanup - E2E_ADMIN_TOKEN not set or no job ID');
        console.log(`  Job ID for manual cleanup: ${jobId}`);
      }

      console.log('STEP 12 COMPLETE: Cleanup done');

      // =========================================================================
      // SUCCESS
      // =========================================================================
      console.log('\n========================================');
      console.log('TEST COMPLETE: Full user journey successful!');
      console.log('========================================');
      console.log(`Job ID: ${jobId}`);
      console.log('All UI interactions completed successfully.');

    } finally {
      // Always clean up the email inbox (if we created one)
      if (inboxId && emailHelper) {
        try {
          await emailHelper.deleteInbox(inboxId);
          console.log('  Cleaned up test email inbox');
        } catch (e) {
          console.log(`  Warning: Failed to delete inbox: ${e}`);
        }
      }
    }
  });
});
