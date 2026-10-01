import { test, expect } from '@playwright/test';
import { URLS } from '../helpers/constants';

/**
 * Production E2E: translated-lyrics preview.
 *
 * The "Add translated lyrics" option at job creation shows a real rendered karaoke
 * frame (default theme, sample lyrics + translation beneath each line). This checks
 * the public preview endpoint serves a JPEG for LTR, RTL and CJK target languages and
 * rejects unsupported ones.
 *
 * Run:
 *   npx playwright test e2e/production/translated-lyrics.spec.ts --config=playwright.production.config.ts
 */

const API_URL = URLS.production.api;

test.describe('Translated lyrics preview', () => {
  // API-only: the prod config's always-on trace hangs on the large image responses
  test.use({ trace: 'off', video: 'off', screenshot: 'off' });

  for (const language of ['es', 'he', 'ja']) {
    test(`renders a preview frame for ${language}`, async ({ request }) => {
      test.setTimeout(60_000);
      const res = await request.get(`${API_URL}/api/themes/translation-preview?language=${language}`);
      expect(res.status()).toBe(200);
      const { image } = await res.json();
      expect(image).toMatch(/^data:image\/jpeg;base64,/);
      // A real 1280x720 frame, not an empty image
      expect(Buffer.from(image.split(',')[1], 'base64').length).toBeGreaterThan(20_000);
    });
  }

  test('rejects an unsupported language', async ({ request }) => {
    const res = await request.get(`${API_URL}/api/themes/translation-preview?language=xx`);
    expect(res.status()).toBe(400);
  });
});
