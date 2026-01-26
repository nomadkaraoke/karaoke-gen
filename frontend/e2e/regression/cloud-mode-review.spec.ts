import { test, expect, Page } from '@playwright/test';
import { setupApiFixtures, setAuthToken, clearAuthToken } from '../fixtures/test-helper';

/**
 * Cloud Mode Review E2E Tests
 *
 * Tests for the cloud mode (hash-based routing) review flow.
 * Cloud mode uses URLs like:
 *   - /app/jobs/#/{jobId}/review
 *   - /app/jobs/#/{jobId}/instrumental
 *
 * Unlike local mode which uses path-based routing:
 *   - /app/jobs/local/review
 *   - /app/jobs/local/instrumental
 *
 * IMPORTANT: These tests verify that cloud mode navigation works correctly
 * and never incorrectly redirects to /local/ paths.
 */

const TEST_JOB_ID = 'test-cloud-job-abc123';

// Sample correction data for cloud mode tests
const mockCorrectionData = {
  original_segments: [
    {
      id: 'seg_1',
      text: 'Hello world',
      start_time: 0.0,
      end_time: 2.5,
      words: [
        { id: 'word_1', text: 'Hello', start_time: 0.0, end_time: 0.5, confidence: 0.95 },
        { id: 'word_2', text: 'world', start_time: 0.5, end_time: 1.0, confidence: 0.92 },
      ],
    },
  ],
  corrected_segments: [
    {
      id: 'seg_1',
      text: 'Hello world',
      start_time: 0.0,
      end_time: 2.5,
      words: [
        { id: 'word_1', text: 'Hello', start_time: 0.0, end_time: 0.5, confidence: 0.95 },
        { id: 'word_2', text: 'world', start_time: 0.5, end_time: 1.0, confidence: 0.92 },
      ],
    },
  ],
  reference_lyrics: {
    genius: {
      segments: [
        {
          id: 'ref_1',
          text: 'Hello world',
          words: [
            { id: 'ref_word_1', text: 'Hello' },
            { id: 'ref_word_2', text: 'world' },
          ],
        },
      ],
    },
  },
  anchor_sequences: [],
  gap_sequences: [],
  corrections: [],
  corrections_made: 0,
  confidence: 0.9,
  metadata: {
    anchor_sequences_count: 0,
    gap_sequences_count: 0,
    total_words: 2,
    correction_ratio: 0,
    audio_hash: 'test_audio_hash_cloud',
    artist: 'Test Artist',
    title: 'Test Song',
    available_handlers: [],
    enabled_handlers: [],
  },
  correction_steps: [],
  word_id_map: {},
  segment_id_map: {},
  resized_segments: [],
  instrumental_options: [
    { id: 'clean', label: 'Clean Instrumental', audio_url: '/api/audio/instrumental_clean' },
    { id: 'with_backing', label: 'With Backing Vocals', audio_url: '/api/audio/instrumental_with_backing' },
  ],
  backing_vocals_analysis: {
    has_backing_vocals: true,
    confidence: 0.85,
    recommendation: 'with_backing',
  },
};

const mockJobData = {
  job_id: TEST_JOB_ID,
  status: 'awaiting_review',
  progress: 50,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  artist: 'Test Artist',
  title: 'Test Song',
  user_email: 'test@example.com',
  audio_hash: 'test_audio_hash_cloud',
};

const mockUserData = {
  email: 'test@example.com',
  name: 'Test User',
  role: 'user',
};

// Setup function to configure mocks for cloud mode
async function setupCloudModeMocks(page: Page) {
  return setupApiFixtures(page, {
    mocks: [
      // Job endpoint - returns mock job
      {
        method: 'GET',
        path: `/api/jobs/${TEST_JOB_ID}`,
        response: { body: mockJobData },
      },
      // Correction data endpoint
      {
        method: 'GET',
        path: `/api/review/${TEST_JOB_ID}/correction-data`,
        response: { body: mockCorrectionData },
      },
      // Submit corrections endpoint
      {
        method: 'POST',
        path: `/api/jobs/${TEST_JOB_ID}/corrections`,
        response: { body: { status: 'success' } },
      },
      // Preview video generation endpoint
      {
        method: 'POST',
        path: `/api/review/${TEST_JOB_ID}/preview-video`,
        response: { body: { status: 'success', preview_hash: 'preview_123' } },
      },
      // Audio endpoint
      {
        method: 'GET',
        path: '/api/review/test_audio_hash_cloud/audio/test_audio_hash_cloud',
        response: { status: 200, body: '' },
      },
      {
        method: 'GET',
        path: `/api/review/${TEST_JOB_ID}/audio/test_audio_hash_cloud`,
        response: { status: 200, body: '' },
      },
      // User endpoint (for auth)
      {
        method: 'GET',
        path: '/api/users/me',
        response: { body: mockUserData },
      },
      // Tenant config endpoint
      {
        method: 'GET',
        path: '/api/tenant/config',
        response: { body: { tenant: null, is_default: true } },
      },
    ],
  });
}

test.describe('Cloud Mode - Hash Route Parsing', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token-123');
    await setupCloudModeMocks(page);
  });

  test('parses hash-based review route correctly', async ({ page }) => {
    // Navigate to cloud mode review URL with hash
    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/review`);
    await page.waitForLoadState('networkidle');

    // Should show the lyrics review UI (not "Page not found")
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });
  });

  test('parses hash-based instrumental route correctly', async ({ page }) => {
    // Update mock to include instrumental options for the job in instrumental state
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: `/api/jobs/${TEST_JOB_ID}`,
          response: { body: { ...mockJobData, status: 'awaiting_review' } },
        },
        {
          method: 'GET',
          path: `/api/review/${TEST_JOB_ID}/correction-data`,
          response: { body: mockCorrectionData },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: mockUserData },
        },
        {
          method: 'GET',
          path: '/api/tenant/config',
          response: { body: { tenant: null, is_default: true } },
        },
      ],
    });

    // Navigate to cloud mode instrumental URL with hash
    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/instrumental`);
    await page.waitForLoadState('networkidle');

    // Should show the instrumental selection UI (not "Page not found")
    await expect(page.locator('h1')).toContainText('Instrumental Selection', { timeout: 10000 });
  });

  test('invalid hash route shows page not found', async ({ page }) => {
    // Navigate to an invalid hash route
    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/invalid`);
    await page.waitForLoadState('networkidle');

    // Should show "Page not found" error
    await expect(page.getByText('Page not found')).toBeVisible({ timeout: 10000 });
  });

  test('empty hash shows page not found', async ({ page }) => {
    // Navigate to URL with empty hash
    await page.goto('/app/jobs/#/');
    await page.waitForLoadState('networkidle');

    // Should show "Page not found" error
    await expect(page.getByText('Page not found')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Cloud Mode - Navigation Flow', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token-123');
    await setupCloudModeMocks(page);
  });

  test('REGRESSION: cloud mode should never use /local/ paths', async ({ page }) => {
    // Track all navigations to ensure we never navigate to /local/
    const navigations: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('/local/')) {
        navigations.push(url);
      }
    });

    // Navigate to cloud mode review
    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/review`);
    await page.waitForLoadState('networkidle');

    // Verify page loaded
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Click Preview Video button to trigger navigation flow
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: 10000 });
    await previewBtn.click();

    // Wait for modal
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 10000 });

    // Click "Proceed to Instrumental Review" button
    const proceedBtn = dialog.getByRole('button', { name: /proceed to instrumental/i });
    await expect(proceedBtn).toBeVisible({ timeout: 5000 });
    await proceedBtn.click();

    // Wait for navigation to complete
    await page.waitForTimeout(1000);

    // Verify we did NOT navigate to /local/
    expect(navigations.filter(url => url.includes('/local/instrumental'))).toHaveLength(0);

    // Verify the URL hash changed to instrumental
    const currentHash = await page.evaluate(() => window.location.hash);
    expect(currentHash).toContain('instrumental');
    expect(currentHash).not.toContain('local');
  });

  test('hash changes trigger UI update', async ({ page }) => {
    // Start on review page
    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/review`);
    await page.waitForLoadState('networkidle');
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Change hash programmatically to instrumental
    await page.evaluate((jobId) => {
      window.location.hash = `/${jobId}/instrumental`;
    }, TEST_JOB_ID);

    // Wait for hash change to be processed
    await page.waitForTimeout(500);

    // Should now show instrumental UI
    await expect(page.locator('h1')).toContainText('Instrumental Selection', { timeout: 10000 });
  });
});

test.describe('Cloud Mode - Auth Requirements', () => {
  test('unauthenticated user sees sign in required', async ({ page }) => {
    await clearAuthToken(page);
    await setupCloudModeMocks(page);

    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/review`);
    await page.waitForLoadState('networkidle');

    // Should show sign in required message
    await expect(page.getByText('Sign in required')).toBeVisible({ timeout: 10000 });
  });

  test('authenticated user can access review', async ({ page }) => {
    await setAuthToken(page, 'test-token-123');
    await setupCloudModeMocks(page);

    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/review`);
    await page.waitForLoadState('networkidle');

    // Should show the review UI
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });
  });
});

test.describe('Cloud Mode vs Local Mode', () => {
  /**
   * These tests verify that local mode and cloud mode remain distinct
   * and don't interfere with each other.
   */

  test('local mode uses path-based routing', async ({ page }) => {
    await clearAuthToken(page);

    // Set up mocks for local mode
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/jobs/local',
          response: { body: { ...mockJobData, job_id: 'local' } },
        },
        {
          method: 'GET',
          path: '/api/review/local/correction-data',
          response: { body: mockCorrectionData },
        },
        {
          method: 'GET',
          path: '/api/tenant/config',
          response: { body: { tenant: null, is_default: true } },
        },
      ],
    });

    // Navigate to local mode review
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Should show the review UI
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Verify URL is path-based, not hash-based
    const url = page.url();
    expect(url).toContain('/local/review');
    expect(url).not.toContain('#');
  });

  test('cloud mode uses hash-based routing', async ({ page }) => {
    await setAuthToken(page, 'test-token-123');
    await setupCloudModeMocks(page);

    // Navigate to cloud mode review
    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/review`);
    await page.waitForLoadState('networkidle');

    // Should show the review UI
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Verify URL is hash-based
    const hash = await page.evaluate(() => window.location.hash);
    expect(hash).toContain(TEST_JOB_ID);
    expect(hash).toContain('review');
  });
});
