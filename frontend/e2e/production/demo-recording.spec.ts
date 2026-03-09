import { test, expect, Page, BrowserContext } from '@playwright/test';

/**
 * Demo Video Recording Test
 *
 * Records a high-resolution (1280x720) walkthrough of the complete karaoke
 * generation flow against production. The recording is post-processed with
 * ffmpeg to create a polished demo video for the homepage.
 *
 * Key differences from happy-path-real-user.spec.ts:
 * - 1280x720 viewport/video (vs 800x450 default)
 * - Deliberate pauses after actions for visual clarity
 * - Markers logged for post-processing (timestamps for speed-up/trim)
 * - Uses pre-configured token only (no signup flow)
 * - No cleanup (keeps the job for manual review)
 *
 * Run with:
 *   E2E_TEST_TOKEN=<token> npx playwright test --config=playwright.demo.config.ts
 *   npm run test:e2e:demo
 *
 * Post-process with:
 *   node scripts/create-demo-video.mjs
 */

// =============================================================================
// CONSTANTS
// =============================================================================

const PROD_URL = 'https://gen.nomadkaraoke.com';

// Use a cached song for faster processing
const TEST_SONG = {
  artist: 'piri',
  title: 'dog',
} as const;

// Deliberate pause durations (ms) for visual pacing in the recording
const PACE = {
  afterNavigation: 2000,    // Let the page settle visually
  afterAction: 1500,        // After clicking buttons, filling fields
  afterStepComplete: 3000,  // Pause at completed states for readability
  scroll: 800,              // Smooth scroll pause
  beforeImportant: 1000,    // Before important actions
} as const;

const TIMEOUTS = {
  action: 30_000,
  expect: 60_000,
  audioSearch: 120_000,
  lyricsProcessing: 1500_000,
  videoRendering: 900_000,
  finalEncoding: 600_000,
  fullTest: 3600_000,
} as const;

// =============================================================================
// HELPERS
// =============================================================================

/**
 * Navigate with retry logic for transient network errors
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
      await page.waitForTimeout(2000 * attempt);
    }
  }
}

/**
 * Log a marker for post-processing. These timestamps help the ffmpeg
 * script know where to speed up, cut, or add overlays.
 */
function marker(label: string): void {
  const now = new Date().toISOString();
  console.log(`[MARKER ${now}] ${label}`);
}

// =============================================================================
// TEST
// =============================================================================

test.describe('Demo Video Recording', () => {
  test.describe.configure({ retries: 0 });

  test('Full karaoke generation flow for demo video', async ({ page, context }) => {
    test.setTimeout(TIMEOUTS.fullTest);

    const token = process.env.E2E_TEST_TOKEN;
    if (!token) {
      test.skip(true, 'E2E_TEST_TOKEN required for demo recording');
      return;
    }

    // Disable YouTube upload for demo jobs
    await page.route('**/api/audio-search/search', async (route) => {
      const request = route.request();
      if (request.method() === 'POST') {
        const postData = request.postDataJSON();
        postData.enable_youtube_upload = false;
        await route.continue({ postData: JSON.stringify(postData) });
      } else {
        await route.continue();
      }
    });

    let jobId: string | null = null;

    // =========================================================================
    // SCENE 1: Landing Page
    // =========================================================================
    marker('SCENE_1_START: Landing Page');

    await gotoWithRetry(page, PROD_URL);
    await expect(page.locator('h1')).toContainText('Karaoke Video', { timeout: TIMEOUTS.expect });
    await page.waitForTimeout(PACE.afterNavigation);

    // Smooth scroll down to show "How It Works" and screenshots
    await page.evaluate(() => window.scrollTo({ top: 600, behavior: 'smooth' }));
    await page.waitForTimeout(2000);

    await page.evaluate(() => window.scrollTo({ top: 1200, behavior: 'smooth' }));
    await page.waitForTimeout(2000);

    // Scroll back to top
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await page.waitForTimeout(PACE.afterStepComplete);

    await page.screenshot({ path: 'test-results/demo-01-landing.png', fullPage: true });
    marker('SCENE_1_END: Landing Page');

    // =========================================================================
    // SCENE 2: Navigate to App & Authenticate
    // =========================================================================
    marker('SCENE_2_START: Authentication');

    // Click "Get Started Free" button to go to the app
    const getStartedBtn = page.getByRole('link', { name: /get started free/i }).first();
    if (await getStartedBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await getStartedBtn.click();
    } else {
      await gotoWithRetry(page, `${PROD_URL}/app`);
    }
    await page.waitForLoadState('networkidle');

    // Inject token
    await page.evaluate((t) => {
      localStorage.setItem('karaoke_access_token', t);
    }, token);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(PACE.afterNavigation);

    await page.screenshot({ path: 'test-results/demo-02-authenticated.png' });
    marker('SCENE_2_END: Authentication');

    // =========================================================================
    // SCENE 3: Create Job - Enter Song Info (Guided Step 1)
    // =========================================================================
    marker('SCENE_3_START: Song Info');

    // Type artist slowly for visual effect
    const artistInput = page.getByTestId('guided-artist-input');
    await artistInput.click();
    await page.waitForTimeout(PACE.beforeImportant);
    await artistInput.pressSequentially(TEST_SONG.artist, { delay: 120 });
    await page.waitForTimeout(PACE.afterAction);

    // Type title
    const titleInput = page.getByTestId('guided-title-input');
    await titleInput.click();
    await titleInput.pressSequentially(TEST_SONG.title, { delay: 120 });
    await page.waitForTimeout(PACE.afterAction);

    await page.screenshot({ path: 'test-results/demo-03-song-info.png' });

    // Click "Choose Audio"
    await page.waitForTimeout(PACE.beforeImportant);
    await page.getByRole('button', { name: /choose audio/i }).click();
    marker('SCENE_3_END: Song Info');

    // =========================================================================
    // SCENE 4: Audio Selection (Guided Step 2)
    // =========================================================================
    marker('SCENE_4_START: Audio Selection');

    // Wait for search results
    await expect(
      page.getByText('Perfect match found')
        .or(page.getByText('Recommended'))
        .or(page.getByText('Limited sources found'))
    ).toBeVisible({ timeout: TIMEOUTS.audioSearch });
    await page.waitForTimeout(PACE.afterNavigation);

    await page.screenshot({ path: 'test-results/demo-04-search-results.png' });

    // Select audio
    const hasPickCard = await page.getByTestId('pick-card').isVisible().catch(() => false);
    if (hasPickCard) {
      await page.waitForTimeout(PACE.beforeImportant);
      await page.getByRole('button', { name: /use this audio/i }).click();
    } else {
      await page.getByRole('button', { name: /^select$/i }).first().click();
    }
    await page.waitForTimeout(PACE.afterAction);

    // Audio edit question — choose "Use audio as-is"
    await page.getByText('Use audio as-is').click({ timeout: TIMEOUTS.action });
    await page.waitForTimeout(PACE.afterAction);
    marker('SCENE_4_END: Audio Selection');

    // =========================================================================
    // SCENE 5: Visibility (Guided Step 3)
    // =========================================================================
    marker('SCENE_5_START: Visibility');

    await expect(page.getByRole('heading', { name: 'How should your video be shared?' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.waitForTimeout(PACE.beforeImportant);
    await page.screenshot({ path: 'test-results/demo-04b-visibility.png' });

    // Choose "Publish & Share" for the demo
    await page.getByRole('button', { name: /publish & share/i }).click();
    await page.waitForTimeout(PACE.afterAction);
    marker('SCENE_5_END: Visibility');

    // =========================================================================
    // SCENE 6: Customize & Create (Guided Step 4)
    // =========================================================================
    marker('SCENE_6_START: Customize & Create');

    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible({ timeout: TIMEOUTS.action });

    // Wait for title card preview to render
    await page.waitForTimeout(4000);
    await page.screenshot({ path: 'test-results/demo-05-customize.png' });

    // Pause to admire the title card preview
    await page.waitForTimeout(PACE.afterStepComplete);

    // Create the job
    await page.getByRole('button', { name: /create karaoke video/i }).click();

    // Wait for success
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: TIMEOUTS.action });
    await page.waitForTimeout(PACE.afterStepComplete);

    // Read job ID
    const createdJobIdEl = page.getByTestId('created-job-id');
    if (await createdJobIdEl.isVisible({ timeout: 5000 }).catch(() => false)) {
      const idText = await createdJobIdEl.textContent() || '';
      const idMatch = idText.match(/ID:\s*([a-f0-9]{8,})/i);
      if (idMatch) jobId = idMatch[1];
    }

    await page.screenshot({ path: 'test-results/demo-06-job-created.png' });
    marker('SCENE_6_END: Customize & Create');

    // =========================================================================
    // SCENE 7: Processing Wait (will be sped up in post-processing)
    // =========================================================================
    marker('SCENE_7_START: Processing Wait (SPEED_UP)');

    // Find job card
    await page.waitForTimeout(3000);
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    if (await refreshBtn.isVisible().catch(() => false)) {
      await refreshBtn.click();
      await page.waitForTimeout(3000);
    }

    const jobCard = page.locator('[class*="rounded-lg"][class*="border"][class*="p-3"]').filter({
      hasText: new RegExp(`ID:\\s*${jobId ? jobId.slice(0, 8) : TEST_SONG.artist}`, 'i')
    }).first();

    // Poll for review readiness
    let foundReview = false;
    const startTime = Date.now();
    while (Date.now() - startTime < TIMEOUTS.lyricsProcessing) {
      if (await refreshBtn.isVisible().catch(() => false)) {
        await refreshBtn.click();
        await page.waitForTimeout(2000);
      }

      const reviewLink = jobCard.getByRole('link', { name: /review lyrics/i });
      if (await reviewLink.isVisible().catch(() => false)) {
        foundReview = true;
        break;
      }

      const statusText = await jobCard.textContent() || '';
      if (statusText.toLowerCase().includes('failed')) {
        throw new Error('Job failed during processing');
      }

      console.log(`  Processing... ${statusText.substring(0, 60)}`);
      await page.waitForTimeout(10000);
    }

    if (!foundReview) throw new Error('Timeout waiting for review');

    await page.screenshot({ path: 'test-results/demo-07-ready-for-review.png' });
    await page.waitForTimeout(PACE.afterStepComplete);
    marker('SCENE_7_END: Processing Wait (SPEED_UP)');

    // =========================================================================
    // SCENE 8: Lyrics Review
    // =========================================================================
    marker('SCENE_8_START: Lyrics Review');

    // Open review page
    const reviewLink = jobCard.getByRole('link', { name: /review lyrics/i });
    const reviewHref = await reviewLink.getAttribute('href');
    const reviewUrl = reviewHref!.startsWith('http') ? reviewHref! : `${PROD_URL}${reviewHref}`;

    const reviewPage = await context.newPage();
    await gotoWithRetry(reviewPage, reviewUrl);
    await page.waitForTimeout(PACE.afterNavigation);

    // Wait for review UI
    try {
      await expect(
        reviewPage.getByRole('button', { name: /preview video/i })
          .or(reviewPage.getByText(/review/i))
      ).toBeVisible({ timeout: TIMEOUTS.action });
    } catch {
      await reviewPage.reload({ waitUntil: 'networkidle' });
      await reviewPage.waitForTimeout(5000);
    }

    await reviewPage.screenshot({ path: 'test-results/demo-08-lyrics-review.png', fullPage: true });
    await reviewPage.waitForTimeout(PACE.afterNavigation);

    // Slowly scroll through the lyrics for visual effect
    await reviewPage.evaluate(() => window.scrollTo({ top: 300, behavior: 'smooth' }));
    await reviewPage.waitForTimeout(1500);
    await reviewPage.evaluate(() => window.scrollTo({ top: 600, behavior: 'smooth' }));
    await reviewPage.waitForTimeout(1500);

    // Scroll to bottom to show Preview Video button
    await reviewPage.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
    await reviewPage.waitForTimeout(PACE.afterAction);

    // Click Preview Video
    const previewVideoBtn = reviewPage.getByRole('button', { name: /preview video/i });
    await expect(previewVideoBtn).toBeVisible({ timeout: TIMEOUTS.action });
    await reviewPage.waitForTimeout(PACE.beforeImportant);
    await previewVideoBtn.click();

    // Wait for preview modal
    const previewModal = reviewPage.getByRole('dialog');
    await expect(previewModal).toBeVisible({ timeout: TIMEOUTS.action });
    await reviewPage.waitForTimeout(5000);

    await reviewPage.screenshot({ path: 'test-results/demo-09-preview-modal.png', fullPage: true });

    // Wait for preview to generate
    const loadingText = reviewPage.getByText(/generating preview video/i);
    try {
      await expect(loadingText).not.toBeVisible({ timeout: 120000 });
    } catch {
      console.log('  Preview generation timed out, continuing');
    }

    await reviewPage.waitForTimeout(PACE.afterStepComplete);
    await reviewPage.screenshot({ path: 'test-results/demo-10-preview-ready.png', fullPage: true });

    // Click Proceed to Instrumental
    const proceedBtn = reviewPage.getByRole('button', { name: /proceed to instrumental/i });
    await expect(proceedBtn).toBeVisible({ timeout: TIMEOUTS.action });
    await reviewPage.waitForTimeout(PACE.beforeImportant);
    await proceedBtn.click();
    await reviewPage.waitForTimeout(3000);

    marker('SCENE_8_END: Lyrics Review');

    // =========================================================================
    // SCENE 9: Instrumental Selection
    // =========================================================================
    marker('SCENE_9_START: Instrumental Selection');

    // Wait for instrumental UI
    try {
      await reviewPage.waitForSelector('.selection-option, .selection-panel, [class*="selection"]', { timeout: 30000 });
    } catch {
      const loadingState = reviewPage.getByText(/loading instrumental/i);
      if (await loadingState.isVisible({ timeout: 5000 }).catch(() => false)) {
        await expect(loadingState).not.toBeVisible({ timeout: 60000 });
      }
    }

    await reviewPage.waitForTimeout(5000);
    await reviewPage.screenshot({ path: 'test-results/demo-11-instrumental.png', fullPage: true });
    await reviewPage.waitForTimeout(PACE.afterNavigation);

    // Select Clean instrumental
    const cleanOption = reviewPage.locator('.selection-option:has-text("Clean")').first();
    if (await cleanOption.isVisible({ timeout: 5000 }).catch(() => false)) {
      await reviewPage.waitForTimeout(PACE.beforeImportant);
      await cleanOption.click();
      await reviewPage.waitForTimeout(PACE.afterAction);
    }

    await reviewPage.screenshot({ path: 'test-results/demo-12-clean-selected.png', fullPage: true });

    // Submit
    const submitBtn = reviewPage.locator('#submit-btn');
    await expect(submitBtn).toBeVisible({ timeout: TIMEOUTS.action });
    await reviewPage.waitForTimeout(PACE.beforeImportant);
    await submitBtn.click();

    await reviewPage.waitForTimeout(5000);
    await reviewPage.screenshot({ path: 'test-results/demo-13-submitted.png', fullPage: true });

    // Handle redirect
    if (!reviewPage.isClosed()) {
      try {
        await reviewPage.waitForURL(/\/app/, { timeout: 10000 });
      } catch {
        await reviewPage.close();
      }
    }

    marker('SCENE_9_END: Instrumental Selection');

    // =========================================================================
    // SCENE 10: Wait for Completion (will be sped up in post-processing)
    // =========================================================================
    marker('SCENE_10_START: Final Rendering (SPEED_UP)');

    await page.bringToFront();
    if (await refreshBtn.isVisible().catch(() => false)) {
      await refreshBtn.click();
      await page.waitForTimeout(3000);
    }

    let isComplete = false;
    const encodeStartTime = Date.now();
    const completionTimeout = TIMEOUTS.videoRendering + TIMEOUTS.finalEncoding + 600_000;

    while (Date.now() - encodeStartTime < completionTimeout) {
      if (await refreshBtn.isVisible().catch(() => false)) {
        await refreshBtn.click();
        await page.waitForTimeout(2000);
      }

      const statusText = await jobCard.textContent() || '';
      if (statusText.toLowerCase().includes('complete') && !statusText.toLowerCase().includes('prep')) {
        isComplete = true;
        break;
      }
      if (statusText.toLowerCase().includes('failed')) {
        throw new Error('Job failed during rendering');
      }

      console.log(`  Rendering... ${statusText.substring(0, 60)}`);
      await page.waitForTimeout(10000);
    }

    if (!isComplete) throw new Error('Timeout waiting for completion');

    await page.waitForTimeout(PACE.afterStepComplete);
    await page.screenshot({ path: 'test-results/demo-14-complete.png' });
    marker('SCENE_10_END: Final Rendering (SPEED_UP)');

    // =========================================================================
    // SCENE 11: Show Completed Job
    // =========================================================================
    marker('SCENE_11_START: Completion');

    // Let the viewer see the completed state with all download badges
    await page.waitForTimeout(5000);
    await page.screenshot({ path: 'test-results/demo-15-final.png' });

    marker('SCENE_11_END: Completion');

    // =========================================================================
    // DONE
    // =========================================================================
    console.log('\n========================================');
    console.log('DEMO RECORDING COMPLETE');
    console.log('========================================');
    console.log(`Job ID: ${jobId}`);
    console.log('');
    console.log('Raw videos are in test-results/');
    console.log('Run: node scripts/create-demo-video.mjs');
  });
});
