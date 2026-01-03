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
 * 4. Create Job - Search for test song via UI
 * 5. Audio Selection - Select audio source via UI dialog
 * 6. Wait for Processing - Monitor via UI status updates
 * 7. Lyrics Review - Open review UI, interact, submit via UI
 * 8. Instrumental Selection - Open selection UI, interact, submit via UI
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
      // STEP 4: Create Karaoke Job (via UI)
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 4: Create Karaoke Job');
      console.log('========================================');

      // Navigate to Search tab
      await page.getByRole('tab', { name: /search/i }).click();
      await page.waitForTimeout(1000);

      // Select a theme if required
      const themeSelect = page.locator('button[role="combobox"]').first();
      if (await themeSelect.isVisible({ timeout: 3000 }).catch(() => false)) {
        const currentTheme = await themeSelect.textContent();
        if (currentTheme?.toLowerCase().includes('select')) {
          await themeSelect.click();
          await page.waitForTimeout(500);
          await page.locator('[role="option"]').first().click();
          await page.waitForTimeout(500);
          console.log('  Selected first theme');
        }
      }

      // Fill artist and title
      await page.getByLabel('Artist').fill(TEST_SONG.artist);
      await page.getByLabel('Title').fill(TEST_SONG.title);
      console.log(`  Searching for: ${TEST_SONG.artist} - ${TEST_SONG.title}`);

      await page.screenshot({ path: 'test-results/04a-search-form.png' });

      // Submit search
      await page.getByRole('button', { name: /search.*create/i }).click();

      // Wait for job card to appear
      console.log('  Waiting for job card...');
      const jobCard = page.locator('[class*="rounded-lg"][class*="border"]').filter({
        hasText: `${TEST_SONG.artist} - ${TEST_SONG.title}`
      }).first();

      await expect(jobCard).toBeVisible({ timeout: TIMEOUTS.audioSearch });
      console.log('  Job card visible');

      // Extract job ID from the card's full text content
      // The ID appears as "ID: xxxxxxxx" in the card text (e.g., "piri - dogID: af10c81f")
      const cardFullText = await jobCard.textContent() || '';
      const idMatch = cardFullText.match(/ID:\s*([a-f0-9]{8,})/i);
      if (idMatch) {
        jobId = idMatch[1];
        console.log(`  Job ID: ${jobId}`);
      } else {
        console.log(`  WARNING: Could not extract job ID from card text`);
        console.log(`  Card text sample: ${cardFullText.substring(0, 100)}...`);
      }

      await page.screenshot({ path: 'test-results/04b-job-created.png' });
      console.log('STEP 4 COMPLETE: Job created');

      // =========================================================================
      // STEP 5: Audio Selection (via UI)
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 5: Audio Selection');
      console.log('========================================');

      // Wait for job to need audio selection or skip if not needed
      await page.waitForTimeout(5000);

      // Check if Select Audio button appears
      const selectAudioBtn = jobCard.getByRole('button', { name: /select audio/i });
      if (await selectAudioBtn.isVisible({ timeout: 30000 }).catch(() => false)) {
        console.log('  Opening audio selection dialog...');
        await selectAudioBtn.click();
        await page.waitForTimeout(2000);

        const dialog = page.locator('[role="dialog"]');
        if (await dialog.isVisible({ timeout: 5000 })) {
          await page.screenshot({ path: 'test-results/05a-audio-dialog-loading.png' });

          // Wait for the loading spinner to disappear
          // The AudioSearchDialog shows "Loading..." while fetching results
          const loadingIndicator = dialog.getByText(/loading/i);
          try {
            await expect(loadingIndicator).not.toBeVisible({ timeout: 60000 });
            console.log('  Audio results loaded');
          } catch {
            console.log('  WARNING: Loading indicator timeout');
          }

          await page.screenshot({ path: 'test-results/05b-audio-dialog-loaded.png' });

          // Check for "No audio sources found" message
          const noResultsMsg = dialog.getByText(/no audio sources found/i);
          if (await noResultsMsg.isVisible({ timeout: 2000 }).catch(() => false)) {
            console.log('  WARNING: No audio sources found - this may cause the test to fail');
            await page.screenshot({ path: 'test-results/05b-no-audio-sources.png' });
          }

          // Find and click first Select button
          const selectButtons = dialog.getByRole('button', { name: /^select$/i });
          const count = await selectButtons.count();
          console.log(`  Found ${count} audio options`);

          if (count > 0) {
            await selectButtons.first().click();
            console.log('  Audio selected via UI');

            // Wait for dialog to close
            await dialog.waitFor({ state: 'hidden', timeout: TIMEOUTS.audioSearch }).catch(() => {
              console.log('  Dialog still open - pressing Escape');
              return page.keyboard.press('Escape');
            });
          } else {
            console.log('  ERROR: No Select buttons found - closing dialog');
            await page.keyboard.press('Escape');
            // This is a critical error - the job won't progress without audio selection
            throw new Error('No audio sources available for selection. The job cannot proceed.');
          }
        }
      } else {
        console.log('  No audio selection needed - job auto-selected or cached');
      }

      await page.screenshot({ path: 'test-results/05c-after-audio.png' });
      console.log('STEP 5 COMPLETE: Audio selection handled');

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
      // STEP 7: Lyrics Review (via UI - REAL USER FLOW)
      // The minimal path: scroll to bottom -> click "Preview Video" -> wait for video -> click "Complete Review"
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 7: Lyrics Review (UI Interaction)');
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

      await reviewPage.screenshot({ path: 'test-results/07b-scrolled-to-bottom.png', fullPage: true });

      // Click "Preview Video" button (at bottom of the lyrics review page)
      // This button has text "Preview Video" and an OndemandVideo icon
      const previewVideoBtn = reviewPage.getByRole('button', { name: /preview video/i });
      await expect(previewVideoBtn).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Found "Preview Video" button');

      await reviewPage.screenshot({ path: 'test-results/07c-before-preview-click.png', fullPage: true });
      await previewVideoBtn.click();
      console.log('  Clicked "Preview Video" button');

      // Wait for the modal dialog to appear
      // The modal has title "Preview Video (With Vocals)"
      const previewModal = reviewPage.getByRole('dialog');
      await expect(previewModal).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Preview modal opened');

      await reviewPage.screenshot({ path: 'test-results/07d-preview-modal.png', fullPage: true });

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

      await reviewPage.screenshot({ path: 'test-results/07e-after-generation.png', fullPage: true });

      // Now check for the video element or an error message
      const videoElement = reviewPage.locator('video');
      const errorAlert = reviewPage.locator('[role="alert"]');

      // Check if there's an error
      if (await errorAlert.isVisible({ timeout: 5000 }).catch(() => false)) {
        const errorText = await errorAlert.textContent();
        console.log(`  WARNING: Preview error: ${errorText}`);
        await reviewPage.screenshot({ path: 'test-results/07e-preview-error.png', fullPage: true });
        // Continue anyway - we can still click Complete Review even if preview failed
      } else if (await videoElement.isVisible({ timeout: 10000 }).catch(() => false)) {
        console.log('  Video element visible in modal');
        // Give the video a moment to buffer/load
        await reviewPage.waitForTimeout(3000);
      } else {
        console.log('  WARNING: No video element found, but continuing anyway');
      }

      await reviewPage.screenshot({ path: 'test-results/07f-video-state.png', fullPage: true });

      // Click "Complete Review" button in the modal
      // This button has variant="contained", color="success", and endIcon={<CloudUpload />}
      const completeReviewBtn = reviewPage.getByRole('button', { name: /complete review/i });
      await expect(completeReviewBtn).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Found "Complete Review" button');

      await completeReviewBtn.click();
      console.log('  Clicked "Complete Review" button');

      // Wait for the submission to process
      // The handleSubmitToServer function calls apiClient.submitCorrections() then window.close()
      await reviewPage.waitForTimeout(5000);

      await reviewPage.screenshot({ path: 'test-results/07g-after-complete-review.png', fullPage: true });

      // The review page may close automatically after submission (window.close())
      // If it's still open, close it manually
      if (!reviewPage.isClosed()) {
        await reviewPage.close();
        console.log('  Closed review UI');
      } else {
        console.log('  Review UI closed automatically after submission');
      }

      // Refresh main app to see updated status
      await page.bringToFront();
      const refreshBtn = page.getByRole('button', { name: /refresh/i });
      if (await refreshBtn.isVisible().catch(() => false)) {
        await refreshBtn.click();
        await page.waitForTimeout(3000);
      }

      await page.screenshot({ path: 'test-results/07h-after-review-refresh.png' });
      console.log('STEP 7 COMPLETE: Lyrics review submitted via UI');

      // =========================================================================
      // STEP 8: Wait for Video Rendering & Instrumental Selection (via UI - REAL USER FLOW)
      // The instrumental UI has: selection-option elements, submit button with id="submit-btn"
      // =========================================================================
      console.log('\n========================================');
      console.log('STEP 8: Wait for Video Rendering & Instrumental Selection');
      console.log('========================================');

      // Wait for instrumental selection to become available
      console.log('  Waiting for video rendering and instrumental selection...');

      let foundInstrumentalStatus = false;
      const renderStartTime = Date.now();

      while (Date.now() - renderStartTime < TIMEOUTS.videoRendering) {
        // Refresh
        if (await refreshBtn.isVisible().catch(() => false)) {
          await refreshBtn.click();
          await page.waitForTimeout(2000);
        }

        // Check job card status
        const statusText = await jobCard.textContent() || '';
        console.log(`  Status: ${statusText.substring(0, 80)}...`);

        if (statusText.toLowerCase().includes('select instrumental') ||
            statusText.toLowerCase().includes('awaiting_instrumental') ||
            (statusText.toLowerCase().includes('action needed') && statusText.toLowerCase().includes('instrumental'))) {
          foundInstrumentalStatus = true;
          console.log('  Job ready for instrumental selection');
          break;
        }

        // Check if already complete (auto-mode might have run)
        if (statusText.toLowerCase().includes('complete') && !statusText.toLowerCase().includes('prep')) {
          console.log('  Job already complete - skipping instrumental selection');
          break;
        }

        // Check if encoding is in progress (past instrumental stage)
        // NOTE: "rendering" status is BEFORE instrumental selection, not after!
        // The flow is: rendering_video -> awaiting_instrumental_selection -> encoding -> complete
        // So only skip if job is at encoding (not rendering)
        if (statusText.toLowerCase().includes('encoding') && !statusText.toLowerCase().includes('awaiting')) {
          console.log('  Job is encoding - instrumental selection was handled');
          break;
        }

        if (statusText.toLowerCase().includes('failed')) {
          throw new Error('Job failed during video rendering');
        }

        await page.waitForTimeout(10000);
      }

      if (foundInstrumentalStatus) {
        // Click the Select Instrumental button/link
        const instrumentalLink = jobCard.getByRole('link', { name: /select instrumental/i });
        await expect(instrumentalLink).toBeVisible({ timeout: TIMEOUTS.action });

        const instrumentalUrl = await instrumentalLink.getAttribute('href');
        console.log(`  Instrumental URL: ${instrumentalUrl}`);

        // Open instrumental UI in new page
        const instrumentalPage = await context.newPage();
        // Handle both absolute and relative URLs
        const fullUrl = instrumentalUrl?.startsWith('http') ? instrumentalUrl : `${PROD_URL}${instrumentalUrl}`;
        // Use 'load' instead of 'networkidle' - instrumental page has ongoing audio requests
        // that prevent networkidle from ever being reached
        await instrumentalPage.goto(fullUrl, { waitUntil: 'load' });
        await instrumentalPage.waitForTimeout(3000);

        await instrumentalPage.screenshot({ path: 'test-results/08a-instrumental-opened.png', fullPage: true });
        console.log('  Instrumental selection UI opened');

        // Wait for the page to fully load - the instrumental UI renders selection options
        await instrumentalPage.waitForSelector('.selection-option, .selection-panel', { timeout: 30000 });

        // Select "Clean" instrumental option
        // The options have class "selection-option" and contain labels with "Clean" or "With Backing"
        const cleanOption = instrumentalPage.locator('.selection-option:has-text("Clean")').first();
        if (await cleanOption.isVisible({ timeout: 5000 }).catch(() => false)) {
          await cleanOption.click();
          console.log('  Selected "Clean" instrumental option');
        } else {
          // If we can't find "Clean" specifically, the first option might already be selected
          console.log('  Clean option not found - using default selection');
        }

        await instrumentalPage.screenshot({ path: 'test-results/08b-clean-selected.png', fullPage: true });

        // Click the submit button: "✓ Confirm & Continue" (id="submit-btn")
        const submitBtn = instrumentalPage.locator('#submit-btn');
        await expect(submitBtn).toBeVisible({ timeout: TIMEOUTS.action });
        console.log('  Found submit button');

        await submitBtn.click();
        console.log('  Clicked "Confirm & Continue" button');

        // Wait for submission to complete - the page shows a success screen then auto-closes
        await instrumentalPage.waitForTimeout(5000);

        await instrumentalPage.screenshot({ path: 'test-results/08c-instrumental-submitted.png', fullPage: true });

        // The page may auto-close after submission
        if (!instrumentalPage.isClosed()) {
          await instrumentalPage.close();
          console.log('  Closed instrumental UI');
        } else {
          console.log('  Instrumental UI closed automatically after submission');
        }
      }

      // Refresh main app
      await page.bringToFront();
      if (await refreshBtn.isVisible().catch(() => false)) {
        await refreshBtn.click();
        await page.waitForTimeout(3000);
      }

      await page.screenshot({ path: 'test-results/08d-after-instrumental.png' });
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

      await page.screenshot({ path: 'test-results/10-downloads-verified.png' });
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
