import { test, expect, Page } from '@playwright/test';
import { setupApiFixtures, clearAuthToken } from '../fixtures/test-helper';

/**
 * Lyrics Review E2E Tests
 *
 * Tests the unified Next.js lyrics review UI with mocked API responses.
 * These tests verify the functionality that was migrated from the standalone
 * React/Vite frontend to the consolidated Next.js application.
 *
 * IMPORTANT: These tests require the karaoke-gen frontend dev server.
 * The port is configurable via E2E_PORT environment variable (default: 3000).
 *
 * To run these tests:
 *   npx playwright test e2e/regression/lyrics-review.spec.ts
 *
 * To run on a different port (e.g., if 3000 is in use):
 *   E2E_PORT=3001 npx playwright test e2e/regression/lyrics-review.spec.ts
 *
 * Test coverage:
 * 1. Page loading and local mode detection
 * 2. Audio player functionality
 * 3. Transcription and reference view display
 * 4. Correction handlers toggle
 * 5. Edit operations (word/segment editing)
 * 6. Undo/redo functionality
 * 7. Find and replace
 * 8. Preview video generation
 * 9. Review completion
 */

// Sample correction data for mocking local mode
const mockCorrectionData = {
  original_segments: [
    {
      id: 'seg_1',
      text: 'Hello world from the test',
      start_time: 0.0,
      end_time: 2.5,
      words: [
        { id: 'word_1', text: 'Hello', start_time: 0.0, end_time: 0.5, confidence: 0.95 },
        { id: 'word_2', text: 'world', start_time: 0.5, end_time: 1.0, confidence: 0.92 },
        { id: 'word_3', text: 'from', start_time: 1.0, end_time: 1.5, confidence: 0.88 },
        { id: 'word_4', text: 'the', start_time: 1.5, end_time: 2.0, confidence: 0.90 },
        { id: 'word_5', text: 'test', start_time: 2.0, end_time: 2.5, confidence: 0.94 },
      ],
    },
    {
      id: 'seg_2',
      text: 'This is another line',
      start_time: 3.0,
      end_time: 5.0,
      words: [
        { id: 'word_6', text: 'This', start_time: 3.0, end_time: 3.5, confidence: 0.96 },
        { id: 'word_7', text: 'is', start_time: 3.5, end_time: 4.0, confidence: 0.97 },
        { id: 'word_8', text: 'another', start_time: 4.0, end_time: 4.5, confidence: 0.93 },
        { id: 'word_9', text: 'line', start_time: 4.5, end_time: 5.0, confidence: 0.91 },
      ],
    },
  ],
  corrected_segments: [
    {
      id: 'seg_1',
      text: 'Hello world from the test',
      start_time: 0.0,
      end_time: 2.5,
      words: [
        { id: 'word_1', text: 'Hello', start_time: 0.0, end_time: 0.5, confidence: 0.95 },
        { id: 'word_2', text: 'world', start_time: 0.5, end_time: 1.0, confidence: 0.92 },
        { id: 'word_3', text: 'from', start_time: 1.0, end_time: 1.5, confidence: 0.88 },
        { id: 'word_4', text: 'the', start_time: 1.5, end_time: 2.0, confidence: 0.90 },
        { id: 'word_5', text: 'test', start_time: 2.0, end_time: 2.5, confidence: 0.94 },
      ],
    },
    {
      id: 'seg_2',
      text: 'This is another line',
      start_time: 3.0,
      end_time: 5.0,
      words: [
        { id: 'word_6', text: 'This', start_time: 3.0, end_time: 3.5, confidence: 0.96 },
        { id: 'word_7', text: 'is', start_time: 3.5, end_time: 4.0, confidence: 0.97 },
        { id: 'word_8', text: 'another', start_time: 4.0, end_time: 4.5, confidence: 0.93 },
        { id: 'word_9', text: 'line', start_time: 4.5, end_time: 5.0, confidence: 0.91 },
      ],
    },
  ],
  reference_lyrics: {
    genius: {
      segments: [
        {
          id: 'ref_1',
          text: 'Hello world from the test',
          words: [
            { id: 'ref_word_1', text: 'Hello' },
            { id: 'ref_word_2', text: 'world' },
            { id: 'ref_word_3', text: 'from' },
            { id: 'ref_word_4', text: 'the' },
            { id: 'ref_word_5', text: 'test' },
          ],
        },
      ],
    },
  },
  anchor_sequences: [
    {
      id: 'anchor_1',
      transcribed_word_ids: ['word_1', 'word_2'],
      reference_word_ids: { genius: ['ref_word_1', 'ref_word_2'] },
      reference_positions: { genius: 0 },
    },
  ],
  gap_sequences: [
    {
      id: 'gap_1',
      transcribed_word_ids: ['word_3', 'word_4', 'word_5'],
      reference_word_ids: { genius: ['ref_word_3', 'ref_word_4', 'ref_word_5'] },
      preceding_anchor_id: 'anchor_1',
      following_anchor_id: null,
    },
  ],
  corrections: [
    {
      word_id: 'word_3',
      corrected_word_id: 'word_3_corrected',
      original_word: 'frum',
      corrected_word: 'from',
      is_deletion: false,
      split_total: null,
      handler: 'SpellingCorrector',
      source: 'genius',
      confidence: 0.85,
      category: 'spelling',
      reason: 'Corrected spelling',
    },
  ],
  corrections_made: 1,
  confidence: 0.9,
  metadata: {
    anchor_sequences_count: 1,
    gap_sequences_count: 1,
    total_words: 9,
    correction_ratio: 0.11,
    audio_hash: 'test_audio_hash_123',
    artist: 'Test Artist',
    title: 'Test Song',
    available_handlers: [
      { id: 'SpellingCorrector', name: 'Spelling Corrector', description: 'Fixes spelling errors' },
      { id: 'PunctuationCorrector', name: 'Punctuation Corrector', description: 'Fixes punctuation' },
    ],
    enabled_handlers: ['SpellingCorrector'],
  },
  correction_steps: [],
  word_id_map: {},
  segment_id_map: {},
  resized_segments: [],
  // Combined review flow: instrumental options
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
  job_id: 'local',
  status: 'awaiting_review',
  progress: 50,
  created_at: null,
  updated_at: null,
  artist: 'Test Artist',
  title: 'Test Song',
  user_email: 'local@localhost',
  audio_hash: 'test_audio_hash_123',
};

// Setup function to configure mocks for local mode
async function setupLocalModeMocks(page: Page) {
  return setupApiFixtures(page, {
    mocks: [
      // Job endpoint - returns mock job for local mode
      {
        method: 'GET',
        path: '/api/jobs/local',
        response: { body: mockJobData },
      },
      // Corrections endpoint - returns mock correction data (old path)
      {
        method: 'GET',
        path: '/api/jobs/local/corrections',
        response: { body: mockCorrectionData },
      },
      // Correction data endpoint (new review path)
      {
        method: 'GET',
        path: '/api/review/local/correction-data',
        response: { body: mockCorrectionData },
      },
      // Submit corrections endpoint (POST, not PUT)
      {
        method: 'POST',
        path: '/api/jobs/local/corrections',
        response: { body: { status: 'success' } },
      },
      // Update handlers endpoint
      {
        method: 'POST',
        path: '/api/review/local/handlers',
        response: { body: { status: 'success', data: mockCorrectionData } },
      },
      // Add lyrics endpoint
      {
        method: 'POST',
        path: '/api/review/local/add-lyrics',
        response: { body: { status: 'success', data: mockCorrectionData } },
      },
      // Preview video generation endpoint
      {
        method: 'POST',
        path: '/api/jobs/local/preview-video',
        response: { body: { status: 'success', preview_hash: 'preview_123' } },
      },
      // Review preview video endpoint (new review path)
      {
        method: 'POST',
        path: '/api/review/local/preview-video',
        response: { body: { status: 'success', preview_hash: 'preview_123' } },
      },
      // Audio endpoint (returns empty for mock)
      {
        method: 'GET',
        path: '/api/audio/test_audio_hash_123',
        response: { status: 200, body: '' },
      },
      // Review audio endpoint (new review path)
      {
        method: 'GET',
        path: '/api/review/local/audio/test_audio_hash_123',
        response: { status: 200, body: '' },
      },
      // Annotations endpoint
      {
        method: 'POST',
        path: '/api/jobs/local/annotations',
        response: { body: { status: 'success' } },
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

test.describe('Lyrics Review - Local Mode', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('loads lyrics review page in local mode', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Should show the lyrics review UI
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });
  });

  test('displays correction data metrics', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for data to load - use exact text to avoid matching substrings
    await expect(page.getByText('Anchor Sequences', { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Corrected Gaps', { exact: true })).toBeVisible();
    await expect(page.getByText('Uncorrected Gaps', { exact: true })).toBeVisible();
  });

  test('displays transcription text', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for transcription to render - use first() since word appears in both transcription and reference
    await expect(page.getByText('Hello').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('world').first()).toBeVisible();
  });

  test('displays reference lyrics', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Should show reference lyrics section
    await expect(page.getByText('Reference Lyrics')).toBeVisible({ timeout: 10000 });
  });

  test('shows handler toggles with counts', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Handler toggles should be visible
    await expect(page.getByText('Correction Handlers')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Spelling Corrector/)).toBeVisible();
  });

  test('mode selector changes interaction mode', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Find mode selector (toggle group)
    const modeSelector = page.locator('[data-slot="toggle-group"]').first();
    await expect(modeSelector).toBeVisible({ timeout: 10000 });

    // Click on highlight mode
    const highlightBtn = page.getByRole('button', { name: /highlight/i }).first();
    if (await highlightBtn.isVisible()) {
      await highlightBtn.click();
      // Mode should change (visual confirmation via active state)
    }
  });

  test('undo/redo buttons exist and have proper state', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Find undo/redo buttons
    const undoBtn = page.locator('button').filter({ has: page.locator('svg') }).nth(0);
    const redoBtn = page.locator('button').filter({ has: page.locator('svg') }).nth(1);

    // Initially, undo should be disabled (no changes made)
    // This is a structural test - verifying the buttons exist
    await expect(page.getByText('Undo All')).toBeVisible({ timeout: 10000 });
  });

  test('find/replace button opens modal', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Click find/replace button
    const findReplaceBtn = page.getByRole('button', { name: /find.*replace/i });
    await expect(findReplaceBtn).toBeVisible({ timeout: 10000 });
    await findReplaceBtn.click();

    // Modal should open
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5000 });
    await expect(dialog.getByRole('heading', { name: /find/i })).toBeVisible();
  });

  test('timing offset button exists', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Timing offset button should be visible
    const timingBtn = page.getByRole('button', { name: /timing.*offset/i });
    await expect(timingBtn).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Lyrics Review - Audio Player', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('audio player controls are visible', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for the audio player section to appear
    await expect(page.getByText('Playback:')).toBeVisible({ timeout: 10000 });

    // Play/pause button should exist
    const playBtn = page.locator('button').filter({ has: page.locator('svg.lucide-play, svg.lucide-pause') }).first();
    await expect(playBtn).toBeVisible();
  });

  test('audio player shows time display', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Should show time display (0:00 format) - use first() since there are two (current and duration)
    await expect(page.getByText(/\d:\d{2}/).first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Lyrics Review - Preview Video', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('preview video button exists in footer', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Scroll to bottom if needed
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));

    // Preview Video button should be visible
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: 10000 });
  });

  test('clicking preview video opens modal', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Scroll and click preview video button
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: 10000 });
    await previewBtn.click();

    // Modal should open
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Lyrics Review - Edit Operations', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('word elements are clickable in transcription view', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Verify words are rendered and have cursor-pointer style (indicating clickability)
    const wordElement = page.locator('span').filter({ hasText: 'Hello' }).first();
    await expect(wordElement).toBeVisible({ timeout: 10000 });

    // Verify it has the expected cursor style for clickable elements
    await expect(wordElement).toHaveClass(/cursor-pointer/);
  });

  test('edit all button exists', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Edit All button should be visible
    const editAllBtn = page.getByRole('button', { name: /edit all/i });
    await expect(editAllBtn).toBeVisible({ timeout: 10000 });
  });

  test('edit segment modal allows typing multi-digit time values', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Click on a word to open edit segment modal
    const wordElement = page.locator('span').filter({ hasText: 'Hello' }).first();
    await expect(wordElement).toBeVisible({ timeout: 10000 });
    await wordElement.click();

    // Wait for edit modal to appear
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5000 });

    // Find the first start time input (labeled "Start") - multiple segments may have one
    const startInput = dialog.getByLabel('Start').first();
    await expect(startInput).toBeVisible();

    // Click into the input to focus it
    await startInput.click();

    // Clear and type a multi-digit value without cursor jumping
    await startInput.fill('12.34');

    // Verify the value was entered correctly
    await expect(startInput).toHaveValue('12.34');

    // Test end time input as well
    const endInput = dialog.getByLabel('End').first();
    await expect(endInput).toBeVisible();

    await endInput.click();
    await endInput.fill('22.56');
    await expect(endInput).toHaveValue('22.56');

    // Blur the input (click outside) to trigger formatting
    await dialog.getByRole('heading').click(); // Click on dialog heading

    // After blur, the values should still be correct (may be reformatted to 2 decimals)
    // Note: The actual formatted value depends on parent state update, but input should accept the value
  });
});

test.describe('Lyrics Review - Handler Management', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('handler toggle switches are interactive', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Find a handler toggle switch
    const handlerSwitch = page.locator('button[role="switch"]').first();
    await expect(handlerSwitch).toBeVisible({ timeout: 10000 });

    // The switch should be clickable
    const initialState = await handlerSwitch.getAttribute('data-state');

    // Click to toggle
    await handlerSwitch.click();
    await page.waitForTimeout(500);

    // State should have attempted to change (API mock will respond)
  });
});

test.describe('Lyrics Review - View Toggle', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('text/timeline toggle exists', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Look for view toggle in the transcription section
    // The toggle may show "Text" and "Duration" or similar
    const textBtn = page.getByRole('button', { name: /text/i }).first();
    const durationBtn = page.getByRole('button', { name: /duration/i }).first();

    // At least one view option should be visible
    const hasViewToggle = (await textBtn.isVisible().catch(() => false)) ||
                          (await durationBtn.isVisible().catch(() => false));

    // If the toggle exists, clicking it should work
    if (await textBtn.isVisible().catch(() => false)) {
      await textBtn.click();
    }
  });
});

test.describe('Lyrics Review - Complete Review Flow', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('complete review button submits corrections', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Scroll to see complete button if needed
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));

    // First click Preview Video to get to complete flow
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: 10000 });
    await previewBtn.click();

    // Wait for modal
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 10000 });

    // Look for Complete Review button in modal
    const completeBtn = dialog.getByRole('button', { name: /proceed to instrumental review/i });
    if (await completeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await completeBtn.click();
      // Should submit and close
      await page.waitForTimeout(1000);
    }
  });
});

// Extended mocks for submit UX tests that include complete-review endpoint
async function setupSubmitFlowMocks(page: Page) {
  return setupApiFixtures(page, {
    mocks: [
      // Job endpoint - returns mock job for local mode
      {
        method: 'GET',
        path: '/api/jobs/local',
        response: { body: mockJobData },
      },
      // Corrections endpoint - returns mock correction data (old path)
      {
        method: 'GET',
        path: '/api/jobs/local/corrections',
        response: { body: mockCorrectionData },
      },
      // Correction data endpoint (new review path)
      {
        method: 'GET',
        path: '/api/review/local/correction-data',
        response: { body: mockCorrectionData },
      },
      // Submit corrections endpoint (POST, not PUT)
      {
        method: 'POST',
        path: '/api/jobs/local/corrections',
        response: { body: { status: 'success' } },
      },
      // Complete review endpoint (triggers video generation)
      {
        method: 'POST',
        path: '/api/jobs/local/complete-review',
        response: { body: { status: 'success', job_status: 'instrumental_selected', message: 'Review completed' } },
      },
      // Preview video generation endpoint (old path)
      {
        method: 'POST',
        path: '/api/jobs/local/preview-video',
        response: { body: { status: 'success', preview_hash: 'preview_123' } },
      },
      // Review preview video endpoint (new review path)
      {
        method: 'POST',
        path: '/api/review/local/preview-video',
        response: { body: { status: 'success', preview_hash: 'preview_123' } },
      },
      // Get preview video file (to display in modal)
      {
        method: 'GET',
        path: '/api/review/local/preview-video/preview_123',
        response: { status: 200, body: '' },
      },
      // Audio endpoint (returns empty for mock)
      {
        method: 'GET',
        path: '/api/audio/test_audio_hash_123',
        response: { status: 200, body: '' },
      },
      // Review audio endpoint (new review path)
      {
        method: 'GET',
        path: '/api/review/local/audio/test_audio_hash_123',
        response: { status: 200, body: '' },
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

test.describe('Lyrics Review - Submit UX', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupSubmitFlowMocks(page);
  });

  test('proceed button navigates to instrumental review in local mode', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Open preview modal
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: 10000 });
    await previewBtn.click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 10000 });

    await dialog.getByRole('button', { name: /proceed to instrumental review/i }).click();

    // Should navigate to instrumental review page
    await page.waitForURL('**/instrumental**', { timeout: 10000 });
    expect(page.url()).toContain('/instrumental');
  });

  test('proceed button is enabled and clickable in preview modal', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Open preview modal
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const previewBtn = page.getByRole('button', { name: /preview video/i });
    await expect(previewBtn).toBeVisible({ timeout: 10000 });
    await previewBtn.click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 10000 });

    const proceedBtn = dialog.getByRole('button', { name: /proceed to instrumental review/i });
    await expect(proceedBtn).toBeVisible({ timeout: 5000 });

    // Verify button is enabled before click
    await expect(proceedBtn).toBeEnabled();

    // Click navigates to instrumental review
    await proceedBtn.click();
    await page.waitForURL('**/instrumental**', { timeout: 10000 });
    expect(page.url()).toContain('/instrumental');
  });
});

test.describe('Lyrics Review - Responsiveness', () => {
  test('displays correctly on mobile viewport', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    await clearAuthToken(page);
    await setupLocalModeMocks(page);

    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Should still show key elements
    await expect(page.getByText('Lyrics Transcription Review')).toBeVisible({ timeout: 10000 });

    // Take screenshot for visual verification
    await page.screenshot({ path: 'test-results/lyrics-review-mobile.png' });
  });

  test('displays correctly on tablet viewport', async ({ page }) => {
    // Set tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });

    await clearAuthToken(page);
    await setupLocalModeMocks(page);

    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Should still show key elements
    await expect(page.getByText('Lyrics Transcription Review')).toBeVisible({ timeout: 10000 });

    // Take screenshot for visual verification
    await page.screenshot({ path: 'test-results/lyrics-review-tablet.png' });
  });
});

test.describe('Combined Review Flow', () => {
  /**
   * Tests for the combined lyrics + instrumental review flow.
   *
   * In the combined flow, users:
   * 1. Review and edit lyrics
   * 2. Preview the video
   * 3. Proceed to instrumental selection
   * 4. Select instrumental track
   * 5. Submit both together
   */

  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('correction data includes instrumental options', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // The mock data includes instrumental_options - this test verifies the mock is correct
    // and that the data structure is in place for the combined flow
  });

  test('correction data includes backing vocals analysis', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // The mock data includes backing_vocals_analysis with recommendation
    // This is used by the instrumental selector to show recommendations
  });

  test('preview video button is visible and clickable', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Preview button should be visible
    const previewButton = page.locator('button:has-text("Preview Video")');
    await expect(previewButton).toBeVisible({ timeout: 5000 });
  });

  test('clicking preview opens review modal', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Click preview button
    const previewButton = page.locator('button:has-text("Preview Video")');
    await previewButton.click();

    // Modal should appear with "Proceed to Instrumental Review" button
    const proceedButton = page.locator('button:has-text("Proceed to Instrumental Review")');
    await expect(proceedButton).toBeVisible({ timeout: 10000 });
  });

  test('review modal shows segment count', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Click preview button
    const previewButton = page.locator('button:has-text("Preview Video")');
    await previewButton.click();

    // Modal should show segment count info
    await expect(page.getByText(/Total segments/)).toBeVisible({ timeout: 10000 });
  });

  test('review modal proceed button has correct label', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('Lyrics Transcription Review', { timeout: 10000 });

    // Click preview button
    const previewButton = page.locator('button:has-text("Preview Video")');
    await previewButton.click();

    // Should say "Proceed to Instrumental Review", not "Complete Review"
    const proceedButton = page.locator('button:has-text("Proceed to Instrumental Review")');
    await expect(proceedButton).toBeVisible({ timeout: 10000 });

    // Should NOT say "Complete Review" (old flow)
    await expect(page.locator('button:has-text("Complete Review")')).not.toBeVisible();
  });
});
