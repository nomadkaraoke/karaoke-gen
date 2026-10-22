/**
 * Fixture Review — Interactive Tier Verification
 *
 * Steps through each search result fixture in the guided flow UI,
 * mocking the API to return that fixture's data. Pauses after each
 * fixture so you can visually verify the tier rendering.
 *
 * Usage:
 *   cd frontend
 *   npm run fixtures:review-ui
 *
 * How to advance:
 *   Open the browser's DevTools console (Cmd+Option+J) and type: next()
 *   This advances to the next fixture.
 *
 * Environment variables:
 *   FIXTURE_START=N      Start from fixture N (1-indexed, default: 1)
 *   FIXTURE_SLUG=slug    Review only the fixture matching this slug
 *   FIXTURE_TIER=1|2|3   Only review fixtures classified as that tier
 */

import { test as base, expect, chromium, type Page, type BrowserContext } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { BEST_CHOICE_MIN_SEEDERS } from '../../lib/audio-search-utils';

// --- Persistent Chrome profile (bookmarks bar enabled) ---

const REVIEW_PROFILE = path.resolve(__dirname, '../../.review-profile');
const PREFS_DIR = path.join(REVIEW_PROFILE, 'Default');
const PREFS_FILE = path.join(PREFS_DIR, 'Preferences');
if (!fs.existsSync(PREFS_FILE)) {
  fs.mkdirSync(PREFS_DIR, { recursive: true });
  fs.writeFileSync(PREFS_FILE, JSON.stringify({
    bookmark_bar: { show_on_all_tabs: true },
  }, null, 2));
}

const PORT = process.env.E2E_PORT || '3000';

const test = base.extend<object, { reviewContext: BrowserContext }>({
  reviewContext: [async ({}, use) => {
    const context = await chromium.launchPersistentContext(REVIEW_PROFILE, {
      headless: false,
      baseURL: `http://127.0.0.1:${PORT}`,
      viewport: { width: 1280, height: 720 },
    });
    await use(context);
    await context.close();
  }, { scope: 'worker' }],
  /* eslint-disable react-hooks/rules-of-hooks */
  page: async ({ reviewContext }, use) => {
    const page = await reviewContext.newPage();
    await use(page);
    await page.close();
  },
  /* eslint-enable react-hooks/rules-of-hooks */
});

// --- Load fixtures ---

const FIXTURES_DIR = path.resolve(__dirname, 'data', 'search-results');
const INDEX_PATH = path.join(FIXTURES_DIR, 'index.json');

interface FixtureEntry {
  slug: string;
  artist: string;
  title: string;
  resultCount: number;
  status: string;
  file: string;
}

interface Fixture {
  artist: string;
  title: string;
  resultCount: number;
  results: any[];
}

function loadIndex(): FixtureEntry[] {
  if (!fs.existsSync(INDEX_PATH)) {
    throw new Error(`No fixture index found at ${INDEX_PATH}. Run: node e2e/fixtures/fetch-search-fixtures.mjs`);
  }
  return JSON.parse(fs.readFileSync(INDEX_PATH, 'utf-8'));
}

function loadFixture(entry: FixtureEntry): Fixture {
  const filePath = path.join(FIXTURES_DIR, entry.file);
  if (!fs.existsSync(filePath)) {
    throw new Error(`Fixture file not found: ${filePath}`);
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
}

// Compute tier for filtering (same logic as audio-search-utils.ts but simplified)
function computeTier(fixture: Fixture): 1 | 2 | 3 {
  if (fixture.results.length === 0) return 3;

  let best: any = null;
  for (const r of fixture.results) {
    if (r.is_lossless && r.quality_data?.media?.toLowerCase() === 'vinyl') continue;
    if (!best) { best = r; continue; }
    const isBestChoice = r.is_lossless && (r.seeders ?? 0) >= BEST_CHOICE_MIN_SEEDERS && r.quality_data?.media?.toLowerCase() !== 'vinyl';
    const currentIsBest = best.is_lossless && (best.seeders ?? 0) >= BEST_CHOICE_MIN_SEEDERS && best.quality_data?.media?.toLowerCase() !== 'vinyl';
    if (isBestChoice && !currentIsBest) best = r;
    else if (isBestChoice && currentIsBest && (r.seeders ?? 0) > (best.seeders ?? 0)) best = r;
  }
  if (!best) best = fixture.results[0];

  const isBestChoice = best.is_lossless && (best.seeders ?? 0) >= BEST_CHOICE_MIN_SEEDERS && best.quality_data?.media?.toLowerCase() !== 'vinyl';
  const hasLossless = fixture.results.some(
    (r: any) => r.is_lossless && r.provider?.toLowerCase() !== 'youtube' && r.quality_data?.media?.toLowerCase() !== 'vinyl'
  );

  if (isBestChoice) return 1;
  if (!hasLossless) return 3;
  return 2;
}

// --- Mocking helpers ---

const APP_PAGE_MOCKS = [
  { method: 'GET', path: '/api/jobs', body: [] },
  {
    method: 'GET',
    path: '/api/users/me',
    body: { user: { email: 'reviewer@test.com', credits: 99, role: 'admin' }, has_session: true },
  },
  { method: 'GET', path: '/api/themes', body: { themes: [] } },
  { method: 'GET', path: '/api/users/credits/packages', body: { packages: [] } },
];

async function setupMocks(page: Page, fixture: Fixture) {
  const fixtureJobId = `fixture-review-${Date.now()}`;

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    const apiPath = url.pathname;

    if (method === 'POST' && apiPath === '/api/audio-search/search') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: fixtureJobId, status: 'audio_search' }),
      });
      return;
    }

    if (method === 'GET' && apiPath.includes('/api/audio-search/') && apiPath.includes('/results')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ results: fixture.results, status: 'search_complete' }),
      });
      return;
    }

    if (method === 'POST' && apiPath.includes('/api/audio-search/') && apiPath.includes('/select')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: fixtureJobId, status: 'downloading' }),
      });
      return;
    }

    for (const mock of APP_PAGE_MOCKS) {
      if (method === mock.method && apiPath === mock.path) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mock.body),
        });
        return;
      }
    }

    await route.continue();
  });

  await page.addInitScript(() => {
    localStorage.setItem('karaoke_access_token', 'fixture-review-token');
  });
}

/**
 * Wait for the reviewer to type next() in the browser console.
 * Injects a global next() function that resolves a promise,
 * then waits for the 'NEXT' console message.
 */
async function waitForNext(page: Page, info: string) {
  // Inject the next() helper and display info
  await page.evaluate((msg) => {
    // Show banner in the page
    const banner = document.createElement('div');
    banner.id = 'fixture-review-banner';
    banner.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#1a1a2e;color:#e0e0e0;padding:8px 16px;font-family:monospace;font-size:13px;z-index:99999;border-top:2px solid #7c3aed;display:flex;justify-content:space-between;align-items:center;';
    banner.innerHTML = `<span>${msg}</span><span style="color:#a78bfa;">Type <b>next()</b> in DevTools console to continue</span>`;
    document.body.appendChild(banner);

    // Expose next() function
    (window as any).__nextResolve = null;
    (window as any).next = () => {
      console.log('NEXT');
      banner.remove();
    };
  }, info);

  // Wait for the 'NEXT' console message
  await page.waitForEvent('console', {
    predicate: (msg) => msg.text() === 'NEXT',
    timeout: 600_000, // 10 minutes
  });
}

// --- Build fixture list ---

const allEntries = loadIndex();
const startIdx = parseInt(process.env.FIXTURE_START || '1', 10) - 1;
const slugFilter = process.env.FIXTURE_SLUG || '';
const tierFilter = process.env.FIXTURE_TIER ? parseInt(process.env.FIXTURE_TIER, 10) : 0;

let entriesToReview = allEntries;

if (slugFilter) {
  entriesToReview = allEntries.filter((e) => e.slug === slugFilter);
  if (entriesToReview.length === 0) {
    console.error(`No fixture found with slug: ${slugFilter}`);
    console.error('Available slugs:', allEntries.map((e) => e.slug).join(', '));
  }
}

if (tierFilter) {
  entriesToReview = entriesToReview.filter((e) => {
    const fixture = loadFixture(e);
    return computeTier(fixture) === tierFilter;
  });
}

if (startIdx > 0) {
  entriesToReview = entriesToReview.slice(startIdx);
}

// --- Generate tests ---

test.describe('Fixture Review', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(600_000);

  for (let i = 0; i < entriesToReview.length; i++) {
    const entry = entriesToReview[i];
    const globalIdx = allEntries.indexOf(entry) + 1;
    const fixture = loadFixture(entry);
    const tier = computeTier(fixture);

    test(`[${globalIdx}/${allEntries.length}] T${tier} | ${entry.artist} - ${entry.title} (${entry.resultCount} results)`, async ({
      page,
    }) => {
      await setupMocks(page, fixture);

      await page.goto('/app');
      await expect(page.getByLabel(/artist/i)).toBeVisible({ timeout: 15_000 });

      await page.getByLabel(/artist/i).fill(entry.artist);
      await page.getByLabel(/title/i).fill(entry.title);

      await page.getByRole('button', { name: /choose audio/i }).click();

      await expect(
        page
          .getByText('Perfect match found')
          .or(page.getByText('Recommended'))
          .or(page.getByText('Limited sources found'))
          .or(page.getByText(/no audio sources/i))
      ).toBeVisible({ timeout: 15_000 });

      console.log(`\n${'='.repeat(60)}`);
      console.log(`  [${globalIdx}/${allEntries.length}] ${entry.artist} - ${entry.title}`);
      console.log(`  Tier: ${tier} | Results: ${entry.resultCount}`);
      console.log(`${'='.repeat(60)}`);

      // Wait for reviewer to type next() in browser console
      await waitForNext(page, `[${globalIdx}/${allEntries.length}] T${tier} — ${entry.artist} - ${entry.title} (${entry.resultCount} results)`);
    });
  }

  if (entriesToReview.length === 0) {
    test('no fixtures to review', async () => {
      console.log('No fixtures matched the filter criteria.');
    });
  }
});
