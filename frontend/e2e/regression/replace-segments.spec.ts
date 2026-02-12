import { test, expect, Page } from '@playwright/test';
import { setupApiFixtures, clearAuthToken } from '../fixtures/test-helper';

/**
 * Replace Segment Lyrics E2E Tests
 *
 * Tests the "Replace Segment Lyrics" mode in Edit All Lyrics, which lets users
 * swap in custom lyrics line-by-line while preserving existing segment timing.
 *
 * To run these tests:
 *   npx playwright test e2e/regression/replace-segments.spec.ts
 */

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
    {
      id: 'seg_3',
      text: 'Final segment here',
      start_time: 6.0,
      end_time: 8.0,
      words: [
        { id: 'word_10', text: 'Final', start_time: 6.0, end_time: 6.7, confidence: 0.90 },
        { id: 'word_11', text: 'segment', start_time: 6.7, end_time: 7.4, confidence: 0.91 },
        { id: 'word_12', text: 'here', start_time: 7.4, end_time: 8.0, confidence: 0.92 },
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
    {
      id: 'seg_3',
      text: 'Final segment here',
      start_time: 6.0,
      end_time: 8.0,
      words: [
        { id: 'word_10', text: 'Final', start_time: 6.0, end_time: 6.7, confidence: 0.90 },
        { id: 'word_11', text: 'segment', start_time: 6.7, end_time: 7.4, confidence: 0.91 },
        { id: 'word_12', text: 'here', start_time: 7.4, end_time: 8.0, confidence: 0.92 },
      ],
    },
  ],
  reference_lyrics: {},
  anchor_sequences: [],
  gap_sequences: [],
  corrections: [],
  corrections_made: 0,
  confidence: 0.9,
  metadata: {
    anchor_sequences_count: 0,
    gap_sequences_count: 0,
    total_words: 12,
    correction_ratio: 0,
    audio_hash: 'test_audio_hash_123',
    artist: 'Test Artist',
    title: 'Test Song',
    available_handlers: [],
    enabled_handlers: [],
  },
  correction_steps: [],
  word_id_map: {},
  segment_id_map: {},
  resized_segments: [],
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

async function setupLocalModeMocks(page: Page) {
  return setupApiFixtures(page, {
    mocks: [
      { method: 'GET', path: '/api/jobs/local', response: { body: mockJobData } },
      { method: 'GET', path: '/api/jobs/local/corrections', response: { body: mockCorrectionData } },
      { method: 'GET', path: '/api/review/local/correction-data', response: { body: mockCorrectionData } },
      { method: 'POST', path: '/api/jobs/local/corrections', response: { body: { status: 'success' } } },
      { method: 'GET', path: '/api/audio/test_audio_hash_123', response: { status: 200, body: '' } },
      { method: 'GET', path: '/api/review/local/audio/test_audio_hash_123', response: { status: 200, body: '' } },
      { method: 'GET', path: '/api/tenant/config', response: { body: { tenant: null, is_default: true } } },
    ],
  });
}

async function openReplaceSegmentsMode(page: Page) {
  await page.goto('/app/jobs/local/review');
  await page.waitForLoadState('networkidle');

  // Click "Edit All" button
  const editAllBtn = page.getByRole('button', { name: /edit all/i });
  await expect(editAllBtn).toBeVisible({ timeout: 10000 });
  await editAllBtn.click();

  // Mode selection should appear
  await expect(page.getByText('Edit All Lyrics')).toBeVisible({ timeout: 5000 });

  // Click "Replace Segment Lyrics"
  const replaceSegmentsCard = page.getByText('Replace Segment Lyrics');
  await expect(replaceSegmentsCard).toBeVisible();
  await replaceSegmentsCard.click();

  // Wait for replace segments dialog
  await expect(page.getByText('Edit lyrics line by line')).toBeVisible({ timeout: 5000 });
}

test.describe('Replace Segment Lyrics', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
    await setupLocalModeMocks(page);
  });

  test('shows Replace Segment Lyrics option in mode selection', async ({ page }) => {
    await page.goto('/app/jobs/local/review');
    await page.waitForLoadState('networkidle');

    const editAllBtn = page.getByRole('button', { name: /edit all/i });
    await expect(editAllBtn).toBeVisible({ timeout: 10000 });
    await editAllBtn.click();

    // Should see all three options
    await expect(page.getByText('Re-sync Existing Lyrics')).toBeVisible();
    await expect(page.getByText('Replace Segment Lyrics')).toBeVisible();
    await expect(page.getByText('Replace All Lyrics')).toBeVisible();
    await expect(page.getByText('Recommended for custom lyrics')).toBeVisible();
  });

  test('textarea shows existing lyrics pre-filled', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    const textarea = page.getByRole('textbox');
    await expect(textarea).toHaveValue(
      'Hello world from the test\nThis is another line\nFinal segment here'
    );
  });

  test('shows matching line count when correct', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    await expect(page.getByText('3/3 lines')).toBeVisible();
  });

  test('Apply is enabled when line count matches', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    const applyBtn = page.getByRole('button', { name: 'Apply' });
    await expect(applyBtn).toBeEnabled();
  });

  test('disables Apply and shows error when extra line added', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    const textarea = page.getByRole('textbox');
    // Add an extra line
    await textarea.fill(
      'Hello world from the test\nThis is another line\nFinal segment here\nExtra line'
    );

    const applyBtn = page.getByRole('button', { name: 'Apply' });
    await expect(applyBtn).toBeDisabled();
    await expect(page.getByText(/1 too many/)).toBeVisible();
    await expect(page.getByText('4/3 lines')).toBeVisible();
  });

  test('disables Apply and shows error when line removed', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    const textarea = page.getByRole('textbox');
    await textarea.fill('Hello world from the test\nThis is another line');

    const applyBtn = page.getByRole('button', { name: 'Apply' });
    await expect(applyBtn).toBeDisabled();
    await expect(page.getByText(/1 too few/)).toBeVisible();
  });

  test('Apply updates modified segment lyrics in the UI', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    const textarea = page.getByRole('textbox');
    await textarea.fill(
      'Hello world from the test\nCustom parody lyrics now\nFinal segment here'
    );

    const applyBtn = page.getByRole('button', { name: 'Apply' });
    await expect(applyBtn).toBeEnabled();
    await applyBtn.click();

    // Dialog should close
    await expect(page.getByText('Edit lyrics line by line')).not.toBeVisible();

    // The modified line should appear in the lyrics view
    await expect(page.getByText('Custom')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('parody')).toBeVisible();

    // Unchanged lines should still be there
    await expect(page.getByText('Hello')).toBeVisible();
    await expect(page.getByText('Final')).toBeVisible();
  });

  test('re-enables Apply after fixing line count mismatch', async ({ page }) => {
    await openReplaceSegmentsMode(page);

    const textarea = page.getByRole('textbox');
    // Add extra line
    await textarea.fill(
      'Hello world from the test\nThis is another line\nFinal segment here\nExtra'
    );
    await expect(page.getByRole('button', { name: 'Apply' })).toBeDisabled();

    // Fix it back to 3 lines
    await textarea.fill(
      'Hello world from the test\nThis is another line\nFinal segment here'
    );
    await expect(page.getByRole('button', { name: 'Apply' })).toBeEnabled();
  });
});
