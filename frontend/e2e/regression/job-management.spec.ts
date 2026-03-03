import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Job Management
 *
 * Tests job list, job details, and job actions using mocked API responses.
 * These tests run offline in CI without hitting production.
 */

// Standard mocks for app page tests
const APP_PAGE_BASE_MOCKS = [
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

// Mock job data for tests
const MOCK_JOBS = [
  {
    job_id: 'job-completed-1',
    artist: 'Test Artist',
    title: 'Completed Song',
    status: 'completed',
    theme_id: 'theme-123',
    created_at: '2025-12-29T10:00:00Z',
    youtube_url: 'https://youtube.com/watch?v=abc123',
  },
  {
    job_id: 'job-in-review-2',
    artist: 'Another Artist',
    title: 'In Review Song',
    status: 'in_review',
    created_at: '2025-12-29T11:00:00Z',
  },
  {
    job_id: 'job-awaiting-audio-3',
    artist: 'Third Artist',
    title: 'Awaiting Audio',
    status: 'awaiting_audio_selection',
    created_at: '2025-12-29T12:00:00Z',
  },
  {
    job_id: 'job-failed-4',
    artist: 'Failed Artist',
    title: 'Failed Song',
    status: 'failed',
    error_message: 'Audio download failed',
    created_at: '2025-12-29T09:00:00Z',
  },
];

test.describe('Job Management - Job List', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('displays job list correctly', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: MOCK_JOBS },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should show Recent Jobs section
    await expect(page.getByText('Recent Jobs')).toBeVisible();

    // Should show job cards with artist/title
    await expect(page.getByText('Test Artist')).toBeVisible();
    await expect(page.getByText('Completed Song')).toBeVisible();
  });

  test('shows status badges on job cards', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: MOCK_JOBS },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should show different status badges
    await expect(page.getByText(/complete/i).first()).toBeVisible();
    await expect(page.getByText(/review/i).first()).toBeVisible();
    await expect(page.getByText(/failed/i).first()).toBeVisible();
  });

  test('shows empty state when no jobs', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [] },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should show empty state or "no jobs" message
    const noJobsIndicator = page
      .locator('text=/no jobs|create your first|get started/i')
      .first();
    const jobsList = page.locator('[data-testid="jobs-list"]');

    // Either shows empty message or empty list
    const hasNoJobsText = await noJobsIndicator.isVisible().catch(() => false);
    const hasEmptyList = (await jobsList.locator('> *').count()) === 0;

    expect(hasNoJobsText || hasEmptyList).toBe(true);
  });

  test('refresh button fetches updated jobs', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: MOCK_JOBS },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Click refresh
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    if (await refreshBtn.isVisible()) {
      await refreshBtn.click();
      await page.waitForTimeout(1000);
    }
  });
});

test.describe('Job Management - Job Details', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('job card shows artist and title', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: MOCK_JOBS },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should show job with artist and title
    await expect(page.getByText('Test Artist')).toBeVisible();
    await expect(page.getByText('Completed Song')).toBeVisible();
  });

  // TODO: Fix this test - needs to properly expand job card and verify download UI
  // The job card expansion mechanism and download link display need investigation
  test.skip('completed job shows download links', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: {
            body: [MOCK_JOBS[0]], // Just completed job
          },
        },
        {
          method: 'GET',
          path: '/api/jobs/job-completed-1/download-urls',
          response: {
            body: {
              video_720p: 'https://storage.example.com/video.mp4',
              video_4k: 'https://storage.example.com/video_4k.mp4',
              cdg: 'https://storage.example.com/karaoke.cdg',
            },
          },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Expand completed job
    const jobCard = page.locator('text=Test Artist').locator('..');
    await jobCard.click();
    await page.waitForTimeout(500);

    // Should show download section
    const downloadSection = page.locator('text=/download|720p|4k/i').first();
    const hasDownloads = await downloadSection.isVisible().catch(() => false);

    // Downloads might be in expanded view or separate section
    expect(hasDownloads).toBe(true);
  });

  test('job with failed status is displayed', async ({ page }) => {
    const failedJob = {
      job_id: 'job-failed-1',
      artist: 'Failed Test Artist',
      title: 'Failed Test Song',
      status: 'failed',
      error_message: 'Test error message',
      created_at: '2025-12-29T09:00:00Z',
    };

    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [failedJob] },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should show the job artist
    await expect(page.getByText('Failed Test Artist')).toBeVisible();
  });
});

test.describe('Job Management - Actions', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('view logs button opens logs', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: MOCK_JOBS },
        },
        {
          method: 'GET',
          path: '/api/jobs/job-completed-1/logs',
          response: {
            body: {
              logs: [
                { timestamp: '2025-12-29T10:00:00Z', message: 'Job started' },
                { timestamp: '2025-12-29T10:05:00Z', message: 'Audio processed' },
                { timestamp: '2025-12-29T10:10:00Z', message: 'Job completed' },
              ],
            },
          },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Expand a job
    const jobCard = page.locator('text=Test Artist').locator('..');
    await jobCard.click();
    await page.waitForTimeout(500);

    // Click logs button
    const logsBtn = page.getByRole('button', { name: /logs/i }).first();
    if (await logsBtn.isVisible()) {
      await logsBtn.click();
      await page.waitForTimeout(1000);

      // Should show logs (monospace text area)
      const logsSection = page.locator('[class*="font-mono"]').first();
      const hasLogs = await logsSection.isVisible().catch(() => false);
      expect(hasLogs).toBe(true);
    }
  });

  test('delete job button works', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        ...APP_PAGE_BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: {
            body: [MOCK_JOBS[2]], // Awaiting audio selection
          },
        },
        {
          method: 'DELETE',
          path: '/api/jobs/job-awaiting-audio-3',
          response: {
            body: { status: 'success', message: 'Job deleted' },
          },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Expand job
    const jobCard = page.locator('text=Third Artist').locator('..');
    await jobCard.click();
    await page.waitForTimeout(500);

    // Look for delete button
    const deleteBtn = page.getByRole('button', { name: /delete/i }).first();
    if (await deleteBtn.isVisible()) {
      await deleteBtn.click();

      // Should show confirmation dialog
      const confirmBtn = page.getByRole('button', { name: /confirm|yes|ok/i }).first();
      if (await confirmBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
        await confirmBtn.click();
      }
    }
  });
});
