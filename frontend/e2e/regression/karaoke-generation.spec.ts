import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Karaoke Generation Flow (3-Step Guided Flow)
 *
 * Tests the 3-step guided job creation:
 *   Step 1: Song Info (artist/title inputs)
 *   Step 2: Choose Audio (search results with confidence tier UI + fallback options)
 *   Step 3: Customize & Create (title card, display overrides, privacy, submit)
 *
 * Uses mocked API responses — runs offline in CI.
 */

// Mock data
const MOCK_SEARCH_RESULTS = [
  {
    index: 0,
    title: 'Bohemian Rhapsody',
    artist: 'Queen',
    is_lossless: true,
    seeders: 85,
    provider: 'other',
    quality: 'FLAC 16bit 44.1kHz',
    quality_data: { format: 'FLAC', bit_depth: 16, sample_rate: 44100, media: 'CD' },
    release_type: 'Album',
    label: 'EMI',
    year: 1975,
  },
  {
    index: 1,
    title: 'Bohemian Rhapsody',
    artist: 'Queen',
    is_lossless: true,
    seeders: 12,
    provider: 'other',
    quality: 'FLAC 16bit 44.1kHz',
    quality_data: { format: 'FLAC', bit_depth: 16, sample_rate: 44100, media: 'CD' },
    release_type: 'Album',
    label: 'Hollywood Records',
    year: 1991,
  },
  {
    index: 2,
    title: 'Bohemian Rhapsody',
    artist: 'Queen',
    is_lossless: false,
    provider: 'YouTube',
    channel: 'Queen Official',
    view_count: 1500000000,
  },
];

// Standard mocks for the app page
const APP_PAGE_MOCKS = [
  {
    method: 'GET',
    path: '/api/jobs',
    response: { body: [] },
  },
  {
    method: 'GET',
    path: '/api/users/me',
    response: {
      body: {
        user: { email: 'test@example.com', credits: 5, role: 'user' },
        has_session: true,
      },
    },
  },
  {
    method: 'GET',
    path: '/api/themes',
    response: { body: { themes: [] } },
  },
  {
    method: 'GET',
    path: '/api/users/credits/packages',
    response: { body: { packages: [] } },
  },
];

// Mocks for a successful search flow (standalone search + create-from-search)
const SEARCH_FLOW_MOCKS = [
  ...APP_PAGE_MOCKS,
  {
    method: 'POST',
    path: '/api/audio-search/search-standalone',
    response: {
      body: {
        search_session_id: 'session-123',
        results: MOCK_SEARCH_RESULTS,
        results_count: MOCK_SEARCH_RESULTS.length,
      },
    },
  },
  {
    method: 'POST',
    path: '/api/jobs/create-from-search',
    response: {
      body: {
        job_id: 'new-job-123',
        status: 'downloading',
        message: 'Job created',
      },
    },
  },
];

test.describe('Step 1: Song Info', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('shows artist and title inputs on load', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('guided-artist-input')).toBeVisible();
    await expect(page.getByTestId('guided-title-input')).toBeVisible();
    await expect(page.getByRole('button', { name: /choose audio/i })).toBeVisible();
  });

  test('shows step indicator with 3 steps', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Step indicator should show 3 labels (use navigation region to avoid matching headings/buttons)
    const stepNav = page.getByLabel('Progress');
    await expect(stepNav.getByText('Song Info')).toBeVisible();
    await expect(stepNav.getByText('Choose Audio')).toBeVisible();
    await expect(stepNav.getByText('Customize & Create')).toBeVisible();
  });

  test('Choose Audio button is disabled without inputs', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    const btn = page.getByRole('button', { name: /choose audio/i });
    await expect(btn).toBeDisabled();
  });

  test('Choose Audio button enables after filling both fields', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');

    const btn = page.getByRole('button', { name: /choose audio/i });
    await expect(btn).toBeEnabled();
  });

  test('shows guidance text about audio sources', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should show compact guidance about audio source options
    await expect(page.getByText('We find the best audio')).toBeVisible();
    await expect(page.getByText('upload a file', { exact: true })).toBeVisible();
    // YouTube URL appears in both guidance row and tip - just check at least one is visible
    await expect(page.getByText('YouTube URL').first()).toBeVisible();
  });

  test('shows tip about specific versions', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText(/specific version/i)).toBeVisible();
    await expect(page.getByText(/Live at Wembley/)).toBeVisible();
  });
});

test.describe('Step 2: Choose Audio - Search Results', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('advances to Step 2 and shows search loading', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });

    // Delay search-standalone response so we can observe the loading state
    await page.route('**/api/audio-search/search-standalone', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 3000));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ search_session_id: 'session-123', results: [], results_count: 0 }),
      });
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Fill and submit Step 1
    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Should show searching state
    await expect(page.getByText('Searching for audio sources')).toBeVisible();
  });

  test('shows pick card with best result after search completes', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Wait for results to load
    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Best result should be the high-seeder lossless one
    await expect(page.getByText('LOSSLESS').first()).toBeVisible();
    await expect(page.getByRole('button', { name: /use this audio/i })).toBeVisible();
  });

  test('shows other options expander', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Wait for results
    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Should show expandable "other options"
    await expect(page.getByText(/(?:show|see).*other option/i)).toBeVisible();
  });

  test('expands other options and shows categorized results', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Expand other options
    await page.getByText(/(?:show|see).*other option/i).click();

    // Should show category headers for remaining results
    await expect(page.getByText(/hide other options/i)).toBeVisible();
  });

  test('shows fallback options (YouTube URL and Upload)', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Fallback buttons should be visible
    await expect(page.getByRole('button', { name: /youtube url/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /upload file/i })).toBeVisible();
  });

  test('YouTube URL button toggles form', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Click YouTube URL button
    await page.getByRole('button', { name: /youtube url/i }).click();

    // Should show URL input form
    await expect(page.locator('input[type="url"]')).toBeVisible();
    await expect(page.getByRole('button', { name: /use this url/i })).toBeVisible();
  });

  test('Upload button toggles file upload form', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Click Upload button
    await page.getByRole('button', { name: /upload file/i }).click();

    // Should show file upload area
    await expect(page.locator('input[type="file"]')).toBeAttached();
    await expect(page.getByRole('button', { name: /upload.*create/i })).toBeVisible();
  });

  test('Back button returns to Step 1', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Click Back (no job cleanup needed — search session expires naturally)
    await page.getByRole('button', { name: /back/i }).click();

    // Should return to Step 1 — artist/title inputs should be visible
    await expect(page.getByTestId('guided-artist-input')).toBeVisible();
    await expect(page.getByTestId('guided-title-input')).toBeVisible();
  });

  test('shows no results message when search returns empty', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_MOCKS,
        {
          method: 'POST',
          path: '/api/audio-search/search-standalone',
          response: {
            body: { search_session_id: 'session-empty', results: [], results_count: 0 },
          },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Nonexistent Artist');
    await page.getByTestId('guided-title-input').fill('Unknown Song');
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Wait for empty results
    await expect(page.getByText('No audio sources found')).toBeVisible({ timeout: 15000 });

    // Fallback options should still be available
    await expect(page.getByRole('button', { name: /youtube url/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /upload file/i })).toBeVisible();
  });
});

test.describe('Step 2 → Step 3: Audio Selection to Customize', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('selecting pick card advances to Step 3', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Click "Use This Audio"
    await page.getByRole('button', { name: /use this audio/i }).click();

    // Should advance to Step 3 - Customize & Create
    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible();
    await expect(page.getByText('Title Card Preview')).toBeVisible();
  });

  test('Step 3 shows display override fields pre-filled from search', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /use this audio/i }).click();

    // Should show display override fields with placeholders matching search values
    await expect(page.locator('#guided-display-artist')).toBeVisible();
    await expect(page.locator('#guided-display-title')).toBeVisible();

    // Labels should indicate "same as above"
    await expect(page.getByText('Title Card Artist')).toBeVisible();
    await expect(page.getByText('Title Card Title')).toBeVisible();
  });

  test('Step 3 shows privacy toggle', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /use this audio/i }).click();

    // Privacy toggle should be visible
    await expect(page.getByText(/private.*no youtube/i)).toBeVisible();
    await expect(page.locator('#guided-private')).toBeVisible();
  });

  test('Step 3 shows "Create Karaoke Video" button', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /use this audio/i }).click();

    await expect(page.getByRole('button', { name: /create karaoke video/i })).toBeVisible();
  });

  test('submitting Step 3 calls selectAudioResult and shows success', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /use this audio/i }).click();

    // Click create
    await page.getByRole('button', { name: /create karaoke video/i }).click();

    // Should show success state
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Queen - Bohemian Rhapsody')).toBeVisible();
    await expect(page.getByText('What happens next')).toBeVisible();
  });

  test('success state shows Create Another button', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /use this audio/i }).click();
    await page.getByRole('button', { name: /create karaoke video/i }).click();

    await expect(page.getByText('Job Created')).toBeVisible({ timeout: 5000 });

    // Create Another should reset the flow
    await page.getByRole('button', { name: /create another/i }).click();
    await expect(page.getByTestId('guided-artist-input')).toBeVisible();
  });

  test('Back from Step 3 returns to Step 2', async ({ page }) => {
    await setupApiFixtures(page, { mocks: SEARCH_FLOW_MOCKS });
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /use this audio/i }).click();

    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible();

    // Click Back
    await page.getByRole('button', { name: /back/i }).click();

    // Should return to Step 2 with results still visible
    await expect(page.getByText('Perfect match found')).toBeVisible();
  });
});

test.describe('Fallback Paths: YouTube URL and Upload', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('YouTube URL path creates job and shows success', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...SEARCH_FLOW_MOCKS,
        {
          method: 'POST',
          path: '/api/jobs/create-from-url',
          response: {
            body: {
              job_id: 'url-job-456',
              status: 'downloading',
            },
          },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill('Queen');
    await page.getByTestId('guided-title-input').fill('Bohemian Rhapsody');
    await page.getByRole('button', { name: /choose audio/i }).click();

    await expect(page.getByText('Perfect match found')).toBeVisible({ timeout: 10000 });

    // Click YouTube URL fallback
    await page.getByRole('button', { name: /youtube url/i }).click();
    await page.locator('input[type="url"]').fill('https://youtube.com/watch?v=test');
    await page.getByRole('button', { name: /use this url/i }).click();

    // Should show success directly (skip Step 3 for fallback paths)
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Job Status Display', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('awaiting_audio_selection jobs are hidden from Recent Jobs', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/jobs',
          response: {
            body: [
              {
                job_id: 'search-job',
                artist: 'Searching Artist',
                title: 'Searching Song',
                status: 'awaiting_audio_selection',
              },
              {
                job_id: 'completed-job',
                artist: 'Done Artist',
                title: 'Done Song',
                status: 'complete',
              },
            ],
          },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: {
            body: {
              user: { email: 'test@example.com', credits: 5, role: 'user' },
              has_session: true,
            },
          },
        },
        {
          method: 'GET',
          path: '/api/themes',
          response: { body: { themes: [] } },
        },
        {
          method: 'GET',
          path: '/api/users/credits/packages',
          response: { body: { packages: [] } },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Completed job should be visible
    await expect(page.getByText('Done Artist')).toBeVisible();

    // awaiting_audio_selection job should NOT be visible in Recent Jobs
    await expect(page.getByText('Searching Artist')).not.toBeVisible();
  });

  test('in_review job shows review status', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/jobs',
          response: {
            body: [
              {
                job_id: 'job-1',
                artist: 'Test Artist',
                title: 'Test Song',
                status: 'in_review',
              },
            ],
          },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: {
            body: {
              user: { email: 'test@example.com', credits: 5, role: 'user' },
              has_session: true,
            },
          },
        },
        {
          method: 'GET',
          path: '/api/themes',
          response: { body: { themes: [] } },
        },
        {
          method: 'GET',
          path: '/api/users/credits/packages',
          response: { body: { packages: [] } },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Test Artist')).toBeVisible();
    await expect(page.getByText(/review/i).first()).toBeVisible();
  });
});
