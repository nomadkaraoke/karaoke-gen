import { test, expect, Page } from '@playwright/test';
import * as path from 'path';
import { setAuthToken, getEnvAuthToken } from '../helpers/auth';
import { createCleanupTracker } from '../helpers/test-cleanup';
import { TEST_SONG, URLS, TIMEOUTS } from '../helpers/constants';

/**
 * Production E2E Tests: Guided Job Creation Flow
 *
 * Tests the 4-step guided job creation flow (Song Info → Choose Audio → Visibility → Customize & Create)
 * against the real production backend. Uses route interception to capture outgoing
 * request bodies while still letting them hit the real API.
 *
 * The audio search step uses a confidence tier system:
 *   - Tier 1 ("Perfect match found"): High-confidence lossless result
 *   - Tier 2 ("Recommended"): Good result with caveats
 *   - Tier 3 ("Limited sources found"): Guidance-first, no prominent pick
 *
 * Supports two modes:
 *   - Production frontend (default): tests against gen.nomadkaraoke.com
 *   - Local frontend: set E2E_LOCAL=1 to test against localhost:3000 (still uses prod backend)
 *
 * Prerequisites:
 *   - KARAOKE_ACCESS_TOKEN: User token with credits
 *   - E2E_ADMIN_TOKEN: Admin token for cleanup (optional, uses KARAOKE_ACCESS_TOKEN as fallback)
 *
 * Run against production:
 *   npx playwright test e2e/production/guided-flow.spec.ts --config=playwright.production.config.ts
 *
 * Run against local frontend (with prod backend):
 *   E2E_LOCAL=1 npx playwright test e2e/production/guided-flow.spec.ts --config=playwright.production.config.ts
 */

const API_URL = URLS.production.api;
const FRONTEND_URL = process.env.E2E_LOCAL
  ? URLS.local.frontend
  : URLS.production.frontend;

// Path to the real FLAC fixture (relative to repo root, resolved from frontend/)
const UPLOAD_FIXTURE_PATH = path.resolve(__dirname, '..', '..', '..', 'tests', 'data', 'waterloo10sec.flac');

function getAdminToken(): string | null {
  return process.env.E2E_ADMIN_TOKEN || getEnvAuthToken() || null;
}

/**
 * Helper: Intercept a route, capture the response body as JSON, and pass through.
 * Returns the raw body buffer to route.fulfill() to avoid double-read issues.
 */
async function fetchAndCapture(
  route: any,
): Promise<{ body: Buffer; json: any; status: number; headers: Record<string, string> }> {
  const response = await route.fetch();
  const body = await response.body();
  let json: any = null;
  try {
    json = JSON.parse(body.toString());
  } catch {
    // Response wasn't JSON — still pass through
  }
  return { body, json, status: response.status(), headers: response.headers() };
}

/**
 * Wait for search results to appear. Matches any confidence tier indicator
 * or no-results message, since we don't know what tier a real search will produce.
 */
function waitForSearchComplete(page: Page) {
  return expect(
    page.getByText('Perfect match found')
      .or(page.getByText('Recommended'))
      .or(page.getByText('Limited sources found'))
      .or(page.getByText(/no audio sources/i))
      .or(page.getByText(/taking longer than expected/i))
  ).toBeVisible({ timeout: TIMEOUTS.apiCall });
}

/**
 * Wait for a pick card (Tier 1 or Tier 2) to appear. If Tier 3 (no pick card),
 * returns false so tests can adapt.
 */
async function waitForPickCard(page: Page): Promise<boolean> {
  try {
    await expect(
      page.getByText('Perfect match found')
        .or(page.getByText('Recommended'))
    ).toBeVisible({ timeout: TIMEOUTS.apiCall });
    return true;
  } catch {
    return false;
  }
}

test.describe('Guided Job Creation Flow', () => {
  let cleanup: ReturnType<typeof createCleanupTracker>;

  test.beforeEach(async ({ page }) => {
    const token = getEnvAuthToken();
    test.skip(!token, 'KARAOKE_ACCESS_TOKEN not set — skipping guided flow tests');

    cleanup = createCleanupTracker(getAdminToken()!, API_URL);

    // Authenticate
    await setAuthToken(page, token!);

    if (process.env.E2E_LOCAL) {
      console.log(`  Mode: LOCAL frontend (${FRONTEND_URL}) → prod backend (${API_URL})`);
    }
  });

  test.afterEach(async () => {
    if (cleanup) {
      console.log('  Cleaning up test jobs...');
      const result = await cleanup.cleanupAll();
      console.log(`  Cleanup: ${result.deleted} deleted, ${result.failed} failed`);
    }
  });

  // ---------------------------------------------------------------------------
  // Test 1: Search → Pick card → Create with defaults
  // ---------------------------------------------------------------------------
  test('search → select pick card → create with defaults', async ({ page, request }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    // Intercept API calls to capture params
    let searchBody: any = null;
    let searchJobId: string | null = null;
    let selectBody: any = null;

    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        searchBody = req.postDataJSON();
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    await page.route('**/api/audio-search/*/select', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        selectBody = req.postDataJSON();
      }
      const { body, status, headers } = await fetchAndCapture(route);
      await route.fulfill({ status, headers, body });
    });

    // Step 1: Fill artist/title
    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    console.log('  Step 1 complete: submitted search');

    // Step 2: Wait for pick card (Tier 1 or 2) and click "Use This Audio"
    const hasPickCard = await waitForPickCard(page);
    test.skip(!hasPickCard, 'Search returned Tier 3 (no pick card) — skipping pick card test');
    console.log('  Step 2: Pick card visible');

    await page.getByRole('button', { name: /use this audio/i }).click();
    console.log('  Step 2 complete: selected pick card');

    // Audio edit question — choose "Use audio as-is"
    await page.getByText('Use audio as-is').click({ timeout: TIMEOUTS.action });
    console.log('  Selected "Use audio as-is"');

    // Step 3: Visibility — choose "Publish & Share" (default, not private)
    await expect(page.getByRole('heading', { name: 'How should your video be shared?' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /publish & share/i }).click();
    console.log('  Step 3 complete: selected Publish & Share');

    // Step 4: Click "Create Karaoke Video" with defaults (no overrides)
    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /create karaoke video/i }).click();

    // Wait for success
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: TIMEOUTS.action });
    console.log('  Step 4 complete: job created');

    // Assert: search body has correct params
    expect(searchBody).toBeTruthy();
    expect(searchBody.artist).toBe(TEST_SONG.artist);
    expect(searchBody.title).toBe(TEST_SONG.title);
    expect(searchBody.is_private).toBeFalsy();
    console.log('  Verified search request params');

    // Assert: select was called with a numeric index
    expect(selectBody).toBeTruthy();
    expect(typeof selectBody.selection_index).toBe('number');
    console.log(`  Verified select request: index=${selectBody.selection_index}`);

    // Assert: backend state via API
    expect(searchJobId).toBeTruthy();
    const token = getAdminToken()!;
    const jobResponse = await request.get(`${API_URL}/api/jobs/${searchJobId}`, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: TIMEOUTS.apiCall,
    });
    expect(jobResponse.ok()).toBe(true);
    const job = await jobResponse.json();
    expect(job.artist.toLowerCase()).toBe(TEST_SONG.artist.toLowerCase());
    expect(job.title.toLowerCase()).toBe(TEST_SONG.title.toLowerCase());
    console.log(`  Verified backend job: ${job.artist} - ${job.title} (status: ${job.status})`);
  });

  // ---------------------------------------------------------------------------
  // Test 2: Search → Select → Custom display names + private
  // ---------------------------------------------------------------------------
  test('search → select → custom display names + private', async ({ page, request }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    let searchJobId: string | null = null;
    let selectCalled = false;

    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    await page.route('**/api/audio-search/*/select', async (route) => {
      selectCalled = true;
      const { body, status, headers } = await fetchAndCapture(route);
      await route.fulfill({ status, headers, body });
    });

    // Step 1: Fill and proceed
    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Step 2: Wait for pick card and select it
    const hasPickCard = await waitForPickCard(page);
    test.skip(!hasPickCard, 'Search returned Tier 3 — skipping pick card test');
    await page.getByRole('button', { name: /use this audio/i }).click();

    // Audio edit question — choose "Use audio as-is"
    await page.getByText('Use audio as-is').click({ timeout: TIMEOUTS.action });
    console.log('  Selected "Use audio as-is"');

    // Step 3: Visibility — choose "Keep Private"
    await expect(page.getByRole('heading', { name: 'How should your video be shared?' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /keep private/i }).click();
    console.log('  Selected Keep Private');

    // Step 4: Fill display overrides and confirm
    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible({ timeout: TIMEOUTS.action });

    await page.locator('#guided-display-artist').fill('PIRI (Display)');
    await page.locator('#guided-display-title').fill('DOG (Display)');
    console.log('  Filled display overrides');

    await page.getByRole('button', { name: /create karaoke video/i }).click();

    // Wait for success
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: TIMEOUTS.action });
    console.log('  Job created with custom display names + private');

    // Assert: selectAudioResult was called (confirms job created via search path)
    expect(selectCalled).toBe(true);
    console.log('  Verified selectAudioResult was called');

    // Assert: backend state — verify the job exists and overrides were applied
    expect(searchJobId).toBeTruthy();
    const token = getAdminToken()!;
    const jobResponse = await request.get(`${API_URL}/api/jobs/${searchJobId}`, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: TIMEOUTS.apiCall,
    });
    expect(jobResponse.ok()).toBe(true);
    const job = await jobResponse.json();
    // Verify core fields
    expect(job.artist.toLowerCase()).toBe(TEST_SONG.artist.toLowerCase());
    expect(job.title.toLowerCase()).toBe(TEST_SONG.title.toLowerCase());
    // Verify overrides from Step 3 were applied to the backend job
    expect(job.is_private).toBe(true);
    expect(job.display_artist).toBe('PIRI (Display)');
    expect(job.display_title).toBe('DOG (Display)');
    console.log(`  Verified backend job: ${job.artist} - ${job.title} (is_private=${job.is_private}, display_artist=${job.display_artist})`);
  });

  // ---------------------------------------------------------------------------
  // Test 3: YouTube URL fallback
  // ---------------------------------------------------------------------------
  test('YouTube URL fallback', async ({ page }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    let urlBody: any = null;
    let urlJobId: string | null = null;

    // Intercept search (to track the search job)
    let searchJobId: string | null = null;
    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    // Intercept URL creation
    await page.route('**/api/jobs/create-from-url', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        urlBody = req.postDataJSON();
        const { body, json, status, headers } = await fetchAndCapture(route);
        urlJobId = json?.job_id;
        if (urlJobId) cleanup.trackJob(urlJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    // Step 1: Fill and proceed
    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Wait for search to complete (any tier indicator or no results)
    await waitForSearchComplete(page);
    console.log('  Search complete, clicking YouTube URL fallback');

    await page.getByRole('button', { name: 'YouTube URL', exact: true }).click();

    // Fill URL and submit
    await page.locator('input[type="url"]').fill('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
    await page.getByRole('button', { name: /use this url/i }).click();

    // Audio edit question — choose "Use audio as-is"
    await page.getByText('Use audio as-is').click({ timeout: TIMEOUTS.action });
    console.log('  Selected "Use audio as-is"');

    // Step 3: Visibility — choose "Publish & Share"
    await expect(page.getByRole('heading', { name: 'How should your video be shared?' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /publish & share/i }).click();
    console.log('  Selected Publish & Share');

    // Step 4: Customize & Create — click create
    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /create karaoke video/i }).click();

    // Wait for success
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: TIMEOUTS.action });
    console.log('  Job created via YouTube URL');

    // Assert: intercepted request body
    expect(urlBody).toBeTruthy();
    expect(urlBody.url).toContain('youtube.com');
    expect(urlBody.artist).toBe(TEST_SONG.artist);
    expect(urlBody.title).toBe(TEST_SONG.title);
    console.log(`  Verified URL request: url=${urlBody.url}, artist=${urlBody.artist}`);
  });

  // ---------------------------------------------------------------------------
  // Test 4: File upload fallback
  // ---------------------------------------------------------------------------
  test('file upload fallback', async ({ page }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    let uploadIntercepted = false;
    let uploadJobId: string | null = null;

    // Intercept search (to track the search job)
    let searchJobId: string | null = null;
    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    // Intercept upload
    await page.route('**/api/jobs/upload', async (route) => {
      uploadIntercepted = true;
      const { body, json, status, headers } = await fetchAndCapture(route);
      uploadJobId = json?.job_id;
      if (uploadJobId) cleanup.trackJob(uploadJobId);
      await route.fulfill({ status, headers, body });
    });

    // Step 1: Fill and proceed
    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Wait for search to complete
    await waitForSearchComplete(page);
    console.log('  Search complete, clicking Upload fallback');

    await page.getByRole('button', { name: 'Upload file', exact: true }).click();

    // Upload the real FLAC fixture file (tests/data/waterloo10sec.flac)
    console.log(`  Uploading fixture: ${UPLOAD_FIXTURE_PATH}`);
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(UPLOAD_FIXTURE_PATH);

    // Click upload submit button
    await page.getByRole('button', { name: /use this file/i }).click();

    // Audio edit question — choose "Use audio as-is"
    await page.getByText('Use audio as-is').click({ timeout: TIMEOUTS.action });
    console.log('  Selected "Use audio as-is"');

    // Step 3: Visibility — choose "Publish & Share"
    await expect(page.getByRole('heading', { name: 'How should your video be shared?' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /publish & share/i }).click();
    console.log('  Selected Publish & Share');

    // Step 4: Customize & Create — click create
    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible({ timeout: TIMEOUTS.action });
    await page.getByRole('button', { name: /create karaoke video/i }).click();

    // Wait for success
    await expect(page.getByText('Job Created')).toBeVisible({ timeout: TIMEOUTS.action });
    console.log('  Job created via file upload');

    // Assert: upload was intercepted
    expect(uploadIntercepted).toBe(true);
    console.log('  Verified upload request was sent');
  });

  // ---------------------------------------------------------------------------
  // Test 5: Back navigation cleans up stale job
  // ---------------------------------------------------------------------------
  test('back navigation cleans up stale job', async ({ page }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    let searchJobId: string | null = null;
    let deleteIntercepted = false;
    let deletedJobId: string | null = null;

    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    // Intercept DELETE calls for job cleanup
    await page.route('**/api/jobs/*', async (route) => {
      const req = route.request();
      if (req.method() === 'DELETE') {
        deleteIntercepted = true;
        // Extract job ID from URL path
        const urlParts = req.url().split('/api/jobs/');
        if (urlParts[1]) {
          deletedJobId = urlParts[1].split('?')[0];
        }
      }
      const { body, status, headers } = await fetchAndCapture(route);
      await route.fulfill({ status, headers, body });
    });

    // Step 1: Fill and proceed
    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Step 2: Wait for results to appear
    await waitForSearchComplete(page);
    console.log(`  Search complete, job ID: ${searchJobId}`);

    // Click "Back"
    await page.getByRole('button', { name: /back/i }).click();

    // Wait a moment for the cleanup request to fire (it's fire-and-forget)
    await page.waitForTimeout(2000);

    // Assert: DELETE was intercepted
    expect(deleteIntercepted).toBe(true);
    console.log(`  Verified DELETE intercepted for job: ${deletedJobId}`);

    // Assert: returned to Step 1 (artist/title inputs visible)
    await expect(page.getByTestId('guided-artist-input')).toBeVisible({ timeout: TIMEOUTS.action });
    await expect(page.getByTestId('guided-title-input')).toBeVisible({ timeout: TIMEOUTS.action });
    console.log('  Returned to Step 1 — artist/title inputs visible');
  });

  // ---------------------------------------------------------------------------
  // Test 6: Fallback options (YouTube URL, Upload file) visible after search
  // ---------------------------------------------------------------------------
  test('fallback options visible after search completes', async ({ page }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    let searchJobId: string | null = null;

    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    // Step 1: Search for test song (will find results, but fallback options should still appear)
    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();
    console.log('  Submitted search');

    // Wait for search to complete (any tier)
    await waitForSearchComplete(page);
    console.log('  Search complete');

    // Assert: fallback options are visible
    await expect(page.getByRole('button', { name: 'YouTube URL', exact: true })).toBeVisible({ timeout: TIMEOUTS.action });
    await expect(page.getByRole('button', { name: 'Upload file', exact: true })).toBeVisible({ timeout: TIMEOUTS.action });
    console.log('  Fallback options (YouTube URL, Upload file) visible');

    // Verify fallback section text (varies by tier)
    await expect(
      page.getByText(/not finding what you need/i)
        .or(page.getByText(/can't find the right version/i))
    ).toBeVisible();
    console.log('  Fallback helper text visible');

    console.log(`  Search job tracked for cleanup: ${searchJobId}`);
  });

  // ---------------------------------------------------------------------------
  // Test 7: Confidence tier indicator present after search
  // ---------------------------------------------------------------------------
  test('shows confidence tier indicator after search', async ({ page }) => {
    test.setTimeout(TIMEOUTS.apiCall * 2);

    let searchJobId: string | null = null;

    await page.route('**/api/audio-search/search', async (route) => {
      const req = route.request();
      if (req.method() === 'POST') {
        const { body, json, status, headers } = await fetchAndCapture(route);
        searchJobId = json?.job_id;
        if (searchJobId) cleanup.trackJob(searchJobId);
        await route.fulfill({ status, headers, body });
      } else {
        await route.continue();
      }
    });

    await page.goto(`${FRONTEND_URL}/app`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('guided-artist-input').fill(TEST_SONG.artist);
    await page.getByTestId('guided-title-input').fill(TEST_SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    // Wait for any tier indicator
    await waitForSearchComplete(page);

    // Verify one of the tier-specific elements is present
    const pickCard = page.getByTestId('pick-card');
    const guidanceBanner = page.getByTestId('guidance-banner');

    const hasPickCard = await pickCard.isVisible().catch(() => false);
    const hasGuidanceBanner = await guidanceBanner.isVisible().catch(() => false);

    // At least one must be present (unless no results at all)
    const noResults = await page.getByText(/no audio sources/i).isVisible().catch(() => false);
    if (!noResults) {
      expect(hasPickCard || hasGuidanceBanner).toBe(true);
      console.log(`  Tier indicator: pick-card=${hasPickCard}, guidance-banner=${hasGuidanceBanner}`);
    } else {
      console.log('  No results returned — no tier indicator expected');
    }

    // If pick card is visible, verify "Use This Audio" button exists
    if (hasPickCard) {
      await expect(page.getByRole('button', { name: /use this audio/i })).toBeVisible();
      console.log('  "Use This Audio" button visible in pick card');

      // Check for tier-specific text
      const isPerfect = await page.getByText('Perfect match found').isVisible().catch(() => false);
      const isRecommended = await page.getByText('Recommended').isVisible().catch(() => false);
      console.log(`  Tier: ${isPerfect ? 'Tier 1 (Perfect)' : isRecommended ? 'Tier 2 (Recommended)' : 'Unknown'}`);
    }

    // If guidance banner, verify tips are shown
    if (hasGuidanceBanner) {
      await expect(page.getByText(/check the filename/i)).toBeVisible();
      console.log('  Guidance tips visible in Tier 3 banner');
    }

    console.log(`  Search job tracked for cleanup: ${searchJobId}`);
  });
});
