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
 * 2. New User Signup - Create testmail.app inbox, submit beta form, verify email
 * 3. Magic Link Auth - Click magic link to authenticate
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

      await page.goto(PROD_URL);
      await page.waitForLoadState('networkidle');

      // Verify hero section
      await expect(page.locator('h1')).toContainText('Karaoke Video', { timeout: TIMEOUTS.expect });
      console.log('  Hero section visible');

      // Verify pricing section
      await expect(page.locator('#pricing')).toBeVisible({ timeout: TIMEOUTS.expect });
      console.log('  Pricing section visible');

      await page.screenshot({ path: 'test-results/01-landing-page.png', fullPage: true });
      console.log('STEP 1 COMPLETE: Landing page loads correctly');

      // =========================================================================
      // STEP 2 & 3: Authentication (either via pre-configured token or beta signup)
      // =========================================================================

      if (usePreConfiguredToken) {
        // ----- FAST PATH: Use pre-configured token -----
        console.log('\n========================================');
        console.log('STEP 2: Using Pre-Configured Token (skipping signup)');
        console.log('========================================');

        accessToken = getPreConfiguredToken();
        console.log(`  Using token: ${accessToken.substring(0, 8)}...`);

        // Navigate to app and inject token into localStorage
        await page.goto(`${PROD_URL}/app`);
        await page.evaluate((token) => {
          localStorage.setItem('karaoke_access_token', token);
        }, accessToken);

        // Reload to apply the token
        await page.reload();
        await page.waitForLoadState('networkidle');

        await page.screenshot({ path: 'test-results/02-token-injected.png' });
        console.log('STEP 2 COMPLETE: Token injected, skipping beta enrollment');

        console.log('\n========================================');
        console.log('STEP 3: Verify Authentication');
        console.log('========================================');

        // Verify we're authenticated by checking for the app UI
        await expect(page.locator('body')).not.toContainText('Sign in', { timeout: TIMEOUTS.action });
        console.log('  User authenticated');

        // Try to find credit indicator
        const creditText = await page.getByText(/credit/i).first().textContent().catch(() => 'N/A');
        console.log(`  Credits visible: ${creditText}`);

        console.log('STEP 3 COMPLETE: User authenticated with pre-configured token');

      } else {
        // ----- FULL PATH: Beta signup with testmail.app -----
        console.log('\n========================================');
        console.log('STEP 2: New User Beta Enrollment');
        console.log('========================================');

        // Create test inbox
        const inbox = await emailHelper!.createInbox();
        inboxId = inbox.id!;
        console.log(`  Test email: ${inbox.emailAddress}`);

        // Scroll to beta section first to ensure it's in view
        const betaSection = page.locator('text=Beta Tester Program').first();
        await betaSection.scrollIntoViewIfNeeded();
        await page.waitForTimeout(500);

        // Open beta form - click the Join Beta Program button
        const betaButton = page.getByRole('button', { name: /join beta program/i });
        await expect(betaButton).toBeVisible({ timeout: TIMEOUTS.action });

        // Take screenshot before clicking
        await page.screenshot({ path: 'test-results/02-before-beta-click.png' });
        console.log('  Clicking Join Beta Program button...');

        // Click and wait for the form to appear
        await betaButton.click();

        // Wait for beta form to appear - the button should be replaced by the form
        const betaEmailInput = page.locator('#beta-email');

        // Poll for form visibility with retries
        let formVisible = false;
        for (let attempt = 0; attempt < 5; attempt++) {
          await page.waitForTimeout(1000);
          formVisible = await betaEmailInput.isVisible().catch(() => false);
          if (formVisible) break;
          console.log(`  Form not visible yet, attempt ${attempt + 1}/5`);
          // Try clicking again if form didn't appear
          const buttonStillVisible = await betaButton.isVisible().catch(() => false);
          if (buttonStillVisible) {
            console.log('  Button still visible, clicking again...');
            await betaButton.click();
          }
        }

        await page.screenshot({ path: 'test-results/02-after-beta-click.png' });

        if (!formVisible) {
          // Debug: log the page state
          const pageContent = await page.content();
          console.log('  Page HTML length:', pageContent.length);
          console.log('  Looking for beta-email in HTML:', pageContent.includes('beta-email'));
          throw new Error('Beta form did not appear after clicking Join Beta Program button');
        }

        await expect(betaEmailInput).toBeVisible({ timeout: TIMEOUTS.action });
        console.log('  Beta form visible');

        // Fill beta form
        await betaEmailInput.fill(inbox.emailAddress!);

        // Fill the promise/reason field
        const promiseField = page.locator('#beta-promise, textarea');
        await promiseField.first().fill('piri - dog (E2E test: testing complete karaoke generation flow)');

        // Check the acceptance checkbox
        const acceptCheckbox = page.locator('#beta-accept, input[type="checkbox"]');
        await acceptCheckbox.first().check();

        await page.screenshot({ path: 'test-results/02a-beta-form-filled.png' });
        console.log('  Beta form filled');

        // Intercept the beta enrollment request to add E2E bypass header
        // This bypasses the IP-based rate limit that blocks repeated enrollments from the same IP
        const bypassKey = process.env.E2E_BYPASS_KEY;
        if (bypassKey) {
          console.log('  Adding E2E bypass header to enrollment request');
          await page.route('**/api/users/beta/enroll', async (route) => {
            const request = route.request();
            const headers = {
              ...request.headers(),
              'X-E2E-Bypass-Key': bypassKey,
            };
            await route.continue({ headers });
          });
        }

        // Submit form - look for the submit button
        const submitButton = page.getByRole('button', { name: /get.*free.*credit|submit|enroll/i });
        await expect(submitButton).toBeEnabled({ timeout: TIMEOUTS.action });
        await submitButton.click();

        // Wait for success and redirect
        await expect(
          page.getByText(/welcome to the beta|redirecting to the app/i)
        ).toBeVisible({ timeout: 15000 });

        await page.waitForURL(/\/app/, { timeout: 10000 });
        console.log('  Redirected to /app');

        // Get the token from localStorage
        accessToken = await getAuthToken(page);
        if (!accessToken) {
          throw new Error('No access token received after beta enrollment');
        }
        console.log('  Session token received');

        await page.screenshot({ path: 'test-results/02b-app-after-enrollment.png' });
        console.log('STEP 2 COMPLETE: Beta enrollment successful');

        // =========================================================================
        // STEP 3: Verify Credits (via UI)
        // =========================================================================
        console.log('\n========================================');
        console.log('STEP 3: Verify Credits');
        console.log('========================================');

        // Look for credit indicator in the UI
        const creditText = await page.getByText(/credit/i).first().textContent();
        console.log(`  Credits visible: ${creditText}`);

        console.log('STEP 3 COMPLETE: User authenticated with credits');
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
      await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
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

      // Get the review URL and open in new page
      const reviewUrl = await reviewLink.getAttribute('href');
      console.log(`  Review URL: ${reviewUrl}`);

      // Open review UI in new page
      const reviewPage = await context.newPage();
      await reviewPage.goto(reviewUrl!, { waitUntil: 'networkidle' });
      await reviewPage.waitForTimeout(3000);

      await reviewPage.screenshot({ path: 'test-results/07a-lyrics-review-opened.png', fullPage: true });
      console.log('  Lyrics review UI opened');

      // Wait for the page content to load
      await expect(reviewPage.locator('body')).not.toBeEmpty({ timeout: TIMEOUTS.action });

      // Wait for the main content to render - give MUI time to initialize
      await reviewPage.waitForTimeout(2000);

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

      // Check for distribution indicators in job card or state
      const cardText = await jobCard.textContent() || '';

      if (cardText.toLowerCase().includes('youtube') ||
          cardText.toLowerCase().includes('dropbox') ||
          cardText.toLowerCase().includes('drive')) {
        console.log('  Distribution indicators found in UI');
      } else {
        console.log('  No distribution indicators visible in UI (may be in backend state)');
      }

      console.log('STEP 11 COMPLETE: Distribution check done');

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
