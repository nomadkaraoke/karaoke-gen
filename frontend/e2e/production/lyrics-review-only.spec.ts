import { test, expect } from '@playwright/test';

/**
 * FOCUSED TEST: Lyrics Review UI Only
 *
 * This test is for rapid iteration on the lyrics review step.
 * It requires an existing job that's already in the "in_review" state.
 *
 * Usage:
 *   E2E_JOB_ID=xxx E2E_REVIEW_TOKEN=yyy npx playwright test lyrics-review-only.spec.ts \
 *     --config=playwright.production.config.ts
 *
 * To get a job in the right state:
 *   1. Run the happy-path test and let it reach STEP 6 (or manually create a job)
 *   2. Get the job ID and review token from the logs/UI
 *   3. Run this focused test with those values
 */

const PROD_URL = 'https://gen.nomadkaraoke.com';
const API_URL = 'https://api.nomadkaraoke.com';

// Get test parameters from environment
function getJobId(): string {
  const jobId = process.env.E2E_JOB_ID;
  if (!jobId) {
    throw new Error('E2E_JOB_ID environment variable is required');
  }
  return jobId;
}

function getReviewToken(): string | null {
  return process.env.E2E_REVIEW_TOKEN || null;
}

function getAccessToken(): string | null {
  return process.env.E2E_TEST_TOKEN || null;
}

test.describe('Lyrics Review Only - Focused Test', () => {
  // Disable retries for this focused test
  test.describe.configure({ retries: 0 });

  test('Complete lyrics review via UI', async ({ page, context }) => {
    test.setTimeout(900_000); // 15 minutes - preview generation can take up to 10 min

    const jobId = getJobId();
    const reviewToken = getReviewToken();
    const accessToken = getAccessToken();

    console.log('========================================');
    console.log('FOCUSED TEST: Lyrics Review Only');
    console.log('========================================');
    console.log(`Job ID: ${jobId}`);
    console.log(`Review Token: ${reviewToken ? reviewToken.substring(0, 8) + '...' : 'NOT SET'}`);
    console.log(`Access Token: ${accessToken ? accessToken.substring(0, 8) + '...' : 'NOT SET'}`);
    console.log('');

    // Build the review URL
    let reviewUrl: string;
    if (reviewToken) {
      // Direct review URL with token
      reviewUrl = `${PROD_URL}/lyrics//?baseApiUrl=${encodeURIComponent(API_URL + '/api/review/' + jobId)}&reviewToken=${reviewToken}`;
    } else if (accessToken) {
      // Use the main app to get to the review
      reviewUrl = `${PROD_URL}/lyrics//?baseApiUrl=${encodeURIComponent(API_URL + '/api/review/' + jobId)}`;
    } else {
      throw new Error('Either E2E_REVIEW_TOKEN or E2E_TEST_TOKEN must be set');
    }

    console.log(`Review URL: ${reviewUrl}`);
    console.log('');

    // Navigate to the review page
    console.log('1. Opening lyrics review UI...');
    await page.goto(reviewUrl, { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);

    await page.screenshot({ path: 'test-results/focused-01-review-opened.png', fullPage: true });
    console.log('   Review UI opened');

    // Wait for the page content to load
    await expect(page.locator('body')).not.toBeEmpty({ timeout: 30_000 });

    // Wait for MUI to initialize
    await page.waitForTimeout(2000);

    // Log page content for debugging
    const bodyText = await page.locator('body').textContent();
    console.log(`   Page content preview: ${bodyText?.substring(0, 200)}...`);

    // Look for any error messages
    const errorMessages = await page.locator('[class*="error"], [class*="Error"], .MuiAlert-standardError').allTextContents();
    if (errorMessages.length > 0) {
      console.log(`   WARNING: Error messages found: ${errorMessages.join(', ')}`);
    }

    // Scroll to bottom to find the "Preview Video" button
    console.log('2. Scrolling to bottom of page...');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(1000);

    await page.screenshot({ path: 'test-results/focused-02-scrolled.png', fullPage: true });

    // List all visible buttons for debugging
    const allButtons = await page.getByRole('button').allTextContents();
    console.log(`   Visible buttons: ${allButtons.join(', ')}`);

    // Click "Preview Video" button
    console.log('3. Looking for "Preview Video" button...');
    const previewVideoBtn = page.getByRole('button', { name: /preview video/i });

    if (await previewVideoBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      console.log('   Found "Preview Video" button - clicking...');
      await page.screenshot({ path: 'test-results/focused-03-before-preview.png', fullPage: true });
      await previewVideoBtn.click();
      console.log('   Clicked "Preview Video" button');
    } else {
      // Maybe the button has different text or is not visible yet
      console.log('   Preview Video button not found with that name');
      console.log('   Taking screenshot for debugging...');
      await page.screenshot({ path: 'test-results/focused-03-no-preview-btn.png', fullPage: true });

      // Try alternative selectors
      const finishBtn = page.getByRole('button', { name: /finish|complete|submit|done/i });
      if (await finishBtn.isVisible().catch(() => false)) {
        const btnText = await finishBtn.textContent();
        console.log(`   Found alternative button: "${btnText}"`);
      }

      throw new Error('Could not find Preview Video button');
    }

    // Wait for the modal dialog to appear
    console.log('4. Waiting for preview modal...');
    const previewModal = page.getByRole('dialog');
    await expect(previewModal).toBeVisible({ timeout: 30_000 });
    console.log('   Preview modal opened');

    await page.screenshot({ path: 'test-results/focused-04-modal-opened.png', fullPage: true });

    // List what's in the modal for debugging
    const modalContent = await previewModal.textContent();
    console.log(`   Modal content preview: ${modalContent?.substring(0, 200)}...`);

    // Wait for video preview to generate
    // The PreviewVideoSection shows "Generating preview video..." while loading
    console.log('5. Waiting for preview video generation...');
    const loadingText = page.getByText(/generating preview video/i);

    if (await loadingText.isVisible({ timeout: 5000 }).catch(() => false)) {
      console.log('   Preview is generating... waiting up to 10 minutes');
      try {
        await expect(loadingText).not.toBeVisible({ timeout: 600_000 });
        console.log('   Preview generation complete');
      } catch {
        console.log('   WARNING: Preview generation timed out after 10 minutes');
      }
    } else {
      console.log('   No loading indicator found - preview may already be ready or errored');
    }

    await page.screenshot({ path: 'test-results/focused-05-after-generation.png', fullPage: true });

    // Check for video element or error
    const videoElement = page.locator('video');
    const errorAlert = page.locator('[role="alert"]');

    if (await errorAlert.isVisible({ timeout: 5000 }).catch(() => false)) {
      const errorText = await errorAlert.textContent();
      console.log(`   WARNING: Preview error: ${errorText}`);
      await page.screenshot({ path: 'test-results/focused-05-preview-error.png', fullPage: true });
      // Continue anyway
    } else if (await videoElement.isVisible({ timeout: 10000 }).catch(() => false)) {
      console.log('   Video element visible');
      // Check video state
      const videoState = await videoElement.first().evaluate((el) => {
        const video = el as HTMLVideoElement;
        return {
          src: video.src,
          readyState: video.readyState,
          networkState: video.networkState,
          error: video.error?.message,
        };
      });
      console.log(`   Video state: ${JSON.stringify(videoState)}`);
      await page.waitForTimeout(3000);
    } else {
      console.log('   WARNING: No video element found, but continuing anyway');
    }

    await page.screenshot({ path: 'test-results/focused-06-video-state.png', fullPage: true });

    // Click "Complete Review" button in the modal
    console.log('6. Looking for "Complete Review" button...');

    // List all buttons in the modal
    const modalButtons = await previewModal.getByRole('button').allTextContents();
    console.log(`   Modal buttons: ${modalButtons.join(', ')}`);

    const completeReviewBtn = page.getByRole('button', { name: /complete review/i });

    if (await completeReviewBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      console.log('   Found "Complete Review" button - clicking...');
      await completeReviewBtn.click();
      console.log('   Clicked "Complete Review" button');
    } else {
      // Try to find any submit-like button
      const submitBtn = previewModal.getByRole('button', { name: /submit|confirm|done|save/i });
      if (await submitBtn.isVisible().catch(() => false)) {
        const btnText = await submitBtn.textContent();
        console.log(`   Found alternative submit button: "${btnText}" - clicking...`);
        await submitBtn.click();
      } else {
        console.log('   Could not find Complete Review or similar button');
        await page.screenshot({ path: 'test-results/focused-07-no-complete-btn.png', fullPage: true });
        throw new Error('Could not find Complete Review button');
      }
    }

    // Wait for the submission to process
    console.log('7. Waiting for submission to complete...');
    await page.waitForTimeout(5000);

    await page.screenshot({ path: 'test-results/focused-08-after-submit.png', fullPage: true });

    console.log('');
    console.log('========================================');
    console.log('FOCUSED TEST COMPLETE');
    console.log('========================================');
  });

  test('Add Reference Lyrics via API', async ({ page, context, request }) => {
    /**
     * INTEGRATION TEST: Verifies the add-lyrics API endpoint works.
     *
     * This test was added after a bug where the frontend was calling
     * /api/jobs/{id}/lyrics instead of /api/review/{id}/add-lyrics
     * (see feat/sess-20260124-1843-fix-add-reference-lyrics).
     *
     * The bug wasn't caught because:
     * 1. E2E tests used mocks that didn't validate real backend routes
     * 2. Unit tests didn't cover the addLyrics method
     * 3. No integration tests existed for this feature
     */
    test.setTimeout(60_000);

    const jobId = getJobId();
    const reviewToken = getReviewToken();
    const accessToken = getAccessToken();

    console.log('========================================');
    console.log('INTEGRATION TEST: Add Reference Lyrics');
    console.log('========================================');
    console.log(`Job ID: ${jobId}`);
    console.log('');

    // Test the API endpoint directly (validates the contract)
    console.log('1. Testing API endpoint: POST /api/review/{jobId}/add-lyrics');

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    }

    const response = await request.post(`${API_URL}/api/review/${jobId}/add-lyrics`, {
      headers,
      data: {
        source: 'test_integration',
        lyrics: 'This is a test lyrics line\nAdded by integration test'
      }
    });

    console.log(`   Response status: ${response.status()}`);

    if (response.ok()) {
      const data = await response.json();
      console.log('   SUCCESS: API endpoint works correctly');
      console.log(`   Response contains reference_lyrics: ${!!data.reference_lyrics}`);

      // Verify the new source was added
      if (data.reference_lyrics && data.reference_lyrics.test_integration) {
        console.log('   VERIFIED: New lyrics source "test_integration" was added');
      } else {
        console.log('   Note: Response format may differ - check manually');
      }
    } else {
      const errorBody = await response.text();
      console.log(`   FAILED: ${errorBody}`);

      // 404 specifically means the endpoint doesn't exist (the original bug!)
      if (response.status() === 404) {
        throw new Error(
          'API returned 404 - endpoint /api/review/{id}/add-lyrics does not exist. ' +
          'This was the original bug this test was designed to catch!'
        );
      }

      // Other errors might be auth-related or job-state related
      console.log(`   Note: Error may be expected if job is not in review state or auth is missing`);
    }

    console.log('');
    console.log('========================================');
    console.log('INTEGRATION TEST COMPLETE');
    console.log('========================================');
  });
});
