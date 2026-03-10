import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken, clearAuthToken } from '../fixtures/test-helper';

/**
 * Admin Job Detail Page Tests
 *
 * Tests the admin job detail page functionality with mocked API responses.
 * These tests run as part of the regression suite on every PR.
 */

// Mock data for tests
const mockAdminUser = {
  user: {
    email: 'admin@nomadkaraoke.com',
    role: 'admin',
    credits: 100,
    is_active: true,
  },
};

const mockJob = {
  job_id: 'test-job-123',
  status: 'complete',
  progress: 100,
  artist: 'Test Artist',
  title: 'Test Song',
  user_email: 'user@example.com',
  created_at: '2026-01-09T10:00:00Z',
  updated_at: '2026-01-09T10:30:00Z',
  url: 'https://youtube.com/watch?v=abc123',
  theme_id: 'nomad',
  enable_cdg: false,
  enable_txt: false,
  enable_youtube_upload: true,
  timeline: [
    { status: 'pending', timestamp: '2026-01-09T10:00:00Z', progress: 0 },
    { status: 'downloading', timestamp: '2026-01-09T10:01:00Z', progress: 10 },
    { status: 'separating_stage1', timestamp: '2026-01-09T10:05:00Z', progress: 30 },
    { status: 'complete', timestamp: '2026-01-09T10:30:00Z', progress: 100 },
  ],
  state_data: {
    youtube_url: 'https://youtube.com/watch?v=xyz789',
    dropbox_link: 'https://dropbox.com/s/abc123',
  },
  file_urls: {
    input: 'gs://bucket/jobs/test-job-123/input.flac',
    stems: {
      instrumental_clean: 'gs://bucket/jobs/test-job-123/stems/clean.flac',
    },
  },
  request_metadata: {
    environment: 'production',
    client_ip: '192.168.1.1',
    user_agent: 'Mozilla/5.0',
    server_version: '0.72.0',
  },
};

const mockJobsList = [mockJob];

// Note: API returns { logs: [...] }, not just the array
const mockLogs = {
  logs: [
    { timestamp: '2026-01-09T10:01:00Z', level: 'INFO', worker: 'audio', message: 'Starting audio download' },
    { timestamp: '2026-01-09T10:05:00Z', level: 'INFO', worker: 'audio', message: 'Audio download complete' },
    { timestamp: '2026-01-09T10:10:00Z', level: 'WARNING', worker: 'lyrics', message: 'Retrying transcription' },
    { timestamp: '2026-01-09T10:30:00Z', level: 'INFO', worker: 'video', message: 'Video encoding complete' },
  ],
};

// Mock files response with signed URLs
const mockFiles = {
  job_id: 'test-job-123',
  artist: 'Test Artist',
  title: 'Test Song',
  files: [
    {
      name: 'input.flac',
      path: 'gs://bucket/jobs/test-job-123/input.flac',
      download_url: 'https://storage.googleapis.com/signed/input.flac',
      category: '',
      file_key: 'input',
    },
    {
      name: 'instrumental_clean.flac',
      path: 'gs://bucket/jobs/test-job-123/stems/instrumental_clean.flac',
      download_url: 'https://storage.googleapis.com/signed/instrumental_clean.flac',
      category: 'stems',
      file_key: 'instrumental_clean',
    },
    {
      name: 'vocals.flac',
      path: 'gs://bucket/jobs/test-job-123/stems/vocals.flac',
      download_url: 'https://storage.googleapis.com/signed/vocals.flac',
      category: 'stems',
      file_key: 'vocals',
    },
    {
      name: 'output.lrc',
      path: 'gs://bucket/jobs/test-job-123/lyrics/output.lrc',
      download_url: 'https://storage.googleapis.com/signed/output.lrc',
      category: 'lyrics',
      file_key: 'lrc',
    },
    {
      name: 'video_720p.mp4',
      path: 'gs://bucket/jobs/test-job-123/finals/video_720p.mp4',
      download_url: 'https://storage.googleapis.com/signed/video_720p.mp4',
      category: 'finals',
      file_key: 'lossy_720p_mp4',
    },
  ],
  total_files: 5,
};

test.describe('Admin Job Detail Page', () => {
  test.beforeEach(async ({ page }) => {
    // Set admin auth token
    await setAuthToken(page, 'mock-admin-token');
  });

  test('navigates from jobs list to detail page', async ({ page }) => {
    // Set up API mocks
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: mockJobsList } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    // Go to jobs list
    await page.goto('/admin/jobs');
    await page.waitForLoadState('networkidle');

    // Click on the job row (the job ID text)
    await page.click('text=test-job-123');

    // Should navigate to detail page (using query param)
    // Note: Next.js may add trailing slash depending on config
    await expect(page).toHaveURL(/\/admin\/jobs\/?\?id=test-job-123/);

    // Job header should be visible (job ID as main heading, artist/title as subtitle)
    await expect(page.locator('h1, h2').filter({ hasText: 'test-job-123' })).toBeVisible();
    await expect(page.getByText('Test Artist - Test Song')).toBeVisible();
  });

  test('displays job metadata sections', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } }, // List endpoint is called on page load
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Overview section should show user and progress info (first() because email appears twice)
    await expect(page.getByText('user@example.com').first()).toBeVisible();
    await expect(page.getByText('100%')).toBeVisible();

    // Timeline should be visible showing job progression
    // Check for status badges in timeline (pending -> downloading -> separating -> complete)
    await expect(page.getByText('Pending').first()).toBeVisible();
    await expect(page.locator('[data-slot="badge"]').filter({ hasText: /complete/i }).first()).toBeVisible();

    // Logs section should be visible (shows "Logs (N)" in the UI)
    await expect(page.getByText(/Logs \(\d+\)/)).toBeVisible();
  });

  test('displays timeline with stage durations', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Timeline should show durations (format: Nm Ns)
    // Check for any duration indicator in the timeline (first() because multiple durations exist)
    await expect(page.getByText(/\d+m\s*\d*s/).first()).toBeVisible();
  });

  test('displays request metadata when available', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click to expand Request Metadata section
    await page.click('text=Request Metadata');

    // Should show metadata values
    await expect(page.getByText('production')).toBeVisible();
    await expect(page.getByText('192.168.1.1')).toBeVisible();
  });

  test('shows error message for failed jobs', async ({ page }) => {
    const failedJob = {
      ...mockJob,
      status: 'failed',
      progress: 45,
      error_message: 'Audio separation timed out after 10 minutes',
      error_details: { worker: 'audio', timeout_seconds: 600 },
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: failedJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Error message should be visible (UI shows error inline without "Error" header)
    await expect(page.getByText('Audio separation timed out after 10 minutes')).toBeVisible();
    // Job status should show as failed
    await expect(page.getByText('failed')).toBeVisible();
  });

  test('back button returns to jobs list', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
        { method: 'GET', path: '/api/jobs', response: { body: mockJobsList } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click back button (ghost button with ArrowLeft icon)
    await page.locator('button:has(svg.lucide-arrow-left)').click();

    // Should navigate to jobs list (no query param)
    // Note: Next.js may add trailing slash depending on config
    await expect(page).toHaveURL(/\/admin\/jobs\/?$/);
  });

  test('handles job not found gracefully', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/nonexistent', response: { status: 404, body: { detail: 'Job not found' } } },
        { method: 'GET', path: '/api/jobs/nonexistent/logs', response: { body: { logs: [] } } },
      ],
    });

    await page.goto('/admin/jobs?id=nonexistent');
    await page.waitForLoadState('networkidle');

    // Should show not found message
    await expect(page.getByText('Job not found')).toBeVisible();
    await expect(page.getByRole('button', { name: /Back to Jobs/i })).toBeVisible();
  });

  test('delete job shows confirmation dialog', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click delete button
    await page.click('button:has-text("Delete")');

    // Confirmation dialog should appear
    await expect(page.getByRole('alertdialog')).toBeVisible();
    // The dialog text includes "job test-job-123" (not the exact string from before)
    await expect(page.getByText(/delete job test-job-123/i)).toBeVisible();

    // Cancel button should close dialog
    await page.click('button:has-text("Cancel")');
    await expect(page.getByRole('alertdialog')).not.toBeVisible();
  });

  test('refresh button reloads job data', async ({ page }) => {
    // Set up API mocks
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Verify initial page loaded correctly - header shows just the job ID
    await expect(page.locator('h1').filter({ hasText: 'test-job-123' })).toBeVisible();
    await expect(page.getByText('Test Artist - Test Song')).toBeVisible();

    // The refresh button is a ghost button with the RefreshCw icon, positioned after the job title
    // It's within the header area, next to the status badge
    // Find button near the h1 header that contains an svg
    const headerSection = page.locator('.flex').filter({ has: page.locator('h1') }).first();
    const refreshButton = headerSection.locator('button').filter({ has: page.locator('svg') }).last();

    // Click refresh
    await refreshButton.click();

    // Wait for refresh to complete
    await page.waitForLoadState('networkidle');

    // Verify the job data is still visible (refresh completed successfully)
    await expect(page.locator('h1').filter({ hasText: 'test-job-123' })).toBeVisible();
    await expect(page.getByText('Test Artist - Test Song')).toBeVisible();
  });

  test('files section shows downloadable files grouped by category', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
        { method: 'GET', path: '/api/admin/jobs/test-job-123/files', response: { body: mockFiles } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click the Files tab (shows file count in parentheses)
    await page.getByRole('tab', { name: /Files \(5\)/ }).click();

    // Wait for the Files tab panel to be visible
    await expect(page.getByRole('tabpanel', { name: /Files/ })).toBeVisible();

    // Should show category names (not headings, just text in divs)
    await expect(page.getByText('Audio Stems')).toBeVisible();
    await expect(page.getByText('Lyrics').first()).toBeVisible();
    await expect(page.getByText('Final Outputs')).toBeVisible();

    // Should show individual files as links
    await expect(page.getByRole('link', { name: /input\.flac/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /instrumental_clean\.flac/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /output\.lrc/ })).toBeVisible();

    // Should show download links with correct URLs
    const downloadLinks = page.locator('a[href*="storage.googleapis.com/signed"]');
    await expect(downloadLinks).toHaveCount(5);
  });

  test('files section handles empty files gracefully', async ({ page }) => {
    const emptyFiles = {
      job_id: 'test-job-123',
      artist: 'Test Artist',
      title: 'Test Song',
      files: [],
      total_files: 0,
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
        { method: 'GET', path: '/api/admin/jobs/test-job-123/files', response: { body: emptyFiles } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click the Files tab (shows 0 files)
    await page.getByRole('tab', { name: /Files \(0\)/ }).click();

    // Should show "no files" message
    await expect(page.getByText('No files available')).toBeVisible();
  });

  test('reset toolbar shows reset options', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Reset buttons are now in a toolbar (not accordion) labeled "Reset to:"
    await expect(page.getByText('Reset to:')).toBeVisible();
    // Check the reset buttons are visible in the toolbar
    // Note: "Start" and "Inst." were removed; "Lyrics" renamed to "Review"
    await expect(page.locator('button:has-text("Audio")').first()).toBeVisible();
    await expect(page.locator('button:has-text("Review")').first()).toBeVisible();
    await expect(page.locator('button:has-text("Reprocess")').first()).toBeVisible();
  });

  test('clicking reset button shows confirmation dialog', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click a reset button (Audio = reset to awaiting_audio_selection)
    await page.locator('button:has-text("Audio")').first().click();

    // Should show confirmation dialog
    await expect(page.getByRole('alertdialog')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Reset Job' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reset' })).toBeVisible();
  });

  test('user email is clickable and navigates to user detail', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
        // Mock user detail page dependencies
        { method: 'GET', path: '/api/users/admin/users/user@example.com', response: { body: { email: 'user@example.com', role: 'user', credits: 10 } } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // User email should be visible and clickable
    const emailLink = page.getByText('user@example.com').first();
    await expect(emailLink).toBeVisible();
    await expect(emailLink).toHaveAttribute('title', 'View user profile');

    // Click email should navigate to user detail
    await emailLink.click();
    await expect(page).toHaveURL(/\/admin\/users\/detail\/?\?email=user%40example\.com/);
  });

  test('YouTube source opens modal with clickable link', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Source should be a clickable button
    const sourceButton = page.locator('button').filter({ hasText: /youtube\.com/ });
    await expect(sourceButton).toBeVisible();

    // Click to open modal, which should contain the clickable YouTube link
    await sourceButton.click();
    const modal = page.getByLabel('Audio Source Details');
    await expect(modal).toBeVisible();
    const sourceLink = modal.locator('a[href*="youtube.com"]');
    await expect(sourceLink).toBeVisible();
    await expect(sourceLink).toHaveAttribute('target', '_blank');
  });

  test('search source shows artist-title text', async ({ page }) => {
    const searchJob = {
      ...mockJob,
      url: undefined,
      audio_search_artist: 'Beatles',
      audio_search_title: 'Hey Jude',
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: searchJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Should show "Beatles - Hey Jude" in the source field
    await expect(page.getByText('Beatles - Hey Jude')).toBeVisible();
  });

  test('upload source shows filename', async ({ page }) => {
    const uploadJob = {
      ...mockJob,
      url: undefined,
      filename: 'my-song.mp3',
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: uploadJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Should show the filename
    await expect(page.getByText('my-song.mp3')).toBeVisible();
  });

  // Note: "Clear Workers" tests removed - feature replaced by Full Restart + Regen Screens

  test('delete outputs button shows confirmation dialog', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click Del Outputs button
    await page.locator('button:has-text("Del Outputs")').click();

    // Should show confirmation dialog
    await expect(page.getByRole('alertdialog')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Delete Job Outputs' })).toBeVisible();
    await expect(page.getByText('YouTube video (if uploaded)')).toBeVisible();
    await expect(page.getByText('Dropbox folder')).toBeVisible();
    await expect(page.getByText('Google Drive files')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete Outputs' })).toBeVisible();
  });

  test('delete outputs shows detailed result dialog on success', async ({ page }) => {
    // Mock successful delete outputs response
    const deleteOutputsResponse = {
      status: 'success',
      job_id: 'test-job-123',
      message: 'All outputs deleted successfully',
      deleted_services: {
        youtube: { status: 'deleted', video_id: 'xyz789' },
        dropbox: { status: 'deleted', path: '/Karaoke/NK001 - Test Artist - Test Song' },
        gdrive: { status: 'skipped', reason: 'No Google Drive files found' },
      },
      cleared_state_data: ['youtube_url', 'youtube_video_id', 'dropbox_link', 'brand_code'],
      outputs_deleted_at: '2026-01-15T10:00:00Z',
    };

    // Set up basic fixtures first (user, jobs list, logs)
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
        { method: 'POST', path: '/api/admin/jobs/test-job-123/delete-outputs', response: { body: deleteOutputsResponse } },
      ],
    });

    // Track job fetch calls to return different responses
    let jobFetchCount = 0;
    await page.route('**/api/jobs/test-job-123', async (route) => {
      jobFetchCount++;
      // Return base job on all fetches (before and after deletion)
      // The UI will show the result dialog from the delete response
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockJob),
      });
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Wait for the Del Outputs button to be enabled
    const delOutputsButton = page.locator('button:has-text("Del Outputs")');
    await expect(delOutputsButton).toBeEnabled({ timeout: 10000 });

    // Click Del Outputs button
    await delOutputsButton.click();

    // Confirm deletion in the alert dialog
    await page.locator('[role="alertdialog"] button:has-text("Delete Outputs")').click();

    // Should show result dialog with detailed information
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Outputs Deleted Successfully')).toBeVisible();

    // Should show service names
    await expect(page.getByText('YouTube').first()).toBeVisible();
    await expect(page.getByText('Dropbox').first()).toBeVisible();
    await expect(page.getByText('Google Drive').first()).toBeVisible();

    // Should show YouTube video ID
    await expect(page.getByText('xyz789')).toBeVisible();

    // Should show Google Drive skipped reason
    await expect(page.getByText('No Google Drive files found')).toBeVisible();

    // Close button should work (use first() since there's also an X close button)
    await page.getByRole('button', { name: 'Close' }).first().click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
  });

  test('delete outputs shows partial success result dialog', async ({ page }) => {
    // Mock partial success response (e.g., YouTube failed)
    const deleteOutputsResponse = {
      status: 'partial_success',
      job_id: 'test-job-123',
      message: 'Some outputs could not be deleted',
      deleted_services: {
        youtube: { status: 'error', error: 'Video not found or already deleted' },
        dropbox: { status: 'deleted', path: '/Karaoke/NK001 - Test Artist - Test Song' },
        gdrive: { status: 'skipped', reason: 'No Google Drive files found' },
      },
      cleared_state_data: ['dropbox_link', 'brand_code'],
      outputs_deleted_at: '2026-01-15T10:00:00Z',
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.route('**/api/admin/jobs/*/delete-outputs', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(deleteOutputsResponse),
      });
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Click Del Outputs and confirm
    await page.locator('button:has-text("Del Outputs")').click();
    await page.getByRole('button', { name: 'Delete Outputs' }).click();

    // Should show partial success dialog
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByText('Partial Success')).toBeVisible();

    // Should show YouTube error
    await expect(page.getByText('Video not found or already deleted')).toBeVisible();

    // Should show Dropbox success
    await expect(page.getByText(/NK001.*Test Artist.*Test Song/)).toBeVisible();
  });

  test('delete outputs button is disabled when outputs already deleted', async ({ page }) => {
    // Job with outputs already deleted
    const jobWithDeletedOutputs = {
      ...mockJob,
      outputs_deleted_at: '2026-01-15T10:00:00Z',
      outputs_deleted_by: 'admin@nomadkaraoke.com',
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: jobWithDeletedOutputs } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Del Outputs button should be disabled
    const delOutputsButton = page.locator('button:has-text("Del Outputs")');
    await expect(delOutputsButton).toBeDisabled();

    // Button should have disabled styling (opacity-50 class applied by shadcn)
    await expect(delOutputsButton).toHaveClass(/disabled:opacity-50/);
  });

  test('shows source details modal for audio search job', async ({ page }) => {
    const audioSearchJob = {
      ...mockJob,
      url: undefined,
      audio_source_type: 'audio_search',
      source_name: 'RED',
      source_id: '26048',
      target_file: '04 - Living in a Bubble.flac',
      filename: '04 - Living in a Bubble.flac',
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: audioSearchJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Source should show as search with provider name
    const sourceButton = page.locator('button').filter({ hasText: /Living in a Bubble/ });
    await expect(sourceButton).toBeVisible();

    // Click to open source modal
    await sourceButton.click();

    // Modal should display source details
    const modal = page.getByLabel('Audio Source Details');
    await expect(modal).toBeVisible();
    await expect(modal.getByText('audio search', { exact: true })).toBeVisible();
    await expect(modal.getByText('RED', { exact: true })).toBeVisible();
    await expect(modal.getByText('26048')).toBeVisible();
    await expect(modal.getByText('04 - Living in a Bubble.flac')).toBeVisible();
  });

  test('shows source details modal for YouTube job', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: mockJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Source should be clickable
    const sourceButton = page.locator('button').filter({ hasText: /youtube\.com/ });
    await expect(sourceButton).toBeVisible();

    // Click to open source modal
    await sourceButton.click();

    // Modal should show YouTube URL
    const modal = page.getByLabel('Audio Source Details');
    await expect(modal).toBeVisible();
    await expect(modal.getByText('https://youtube.com/watch?v=abc123')).toBeVisible();
  });

  test('shows source details modal for file upload job', async ({ page }) => {
    const uploadJob = {
      ...mockJob,
      url: undefined,
      audio_source_type: 'file_upload',
      filename: 'my-song.mp3',
    };

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/jobs', response: { body: [] } },
        { method: 'GET', path: '/api/jobs/test-job-123', response: { body: uploadJob } },
        { method: 'GET', path: '/api/jobs/test-job-123/logs', response: { body: mockLogs } },
      ],
    });

    await page.goto('/admin/jobs?id=test-job-123');
    await page.waitForLoadState('networkidle');

    // Source should show filename
    const sourceButton = page.locator('button').filter({ hasText: /my-song\.mp3/ });
    await expect(sourceButton).toBeVisible();

    // Click to open source modal
    await sourceButton.click();

    // Modal should show upload details
    const modal = page.getByLabel('Audio Source Details');
    await expect(modal).toBeVisible();
    await expect(modal.getByText('file upload')).toBeVisible();
    await expect(modal.getByText('my-song.mp3')).toBeVisible();
  });
});
