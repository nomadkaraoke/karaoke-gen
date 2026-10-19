import { test, expect } from '@playwright/test';

/**
 * requests.nomadkaraoke.com serves the voting board on its own host (2026-10-19).
 *
 * The link we paste in YouTube replies/descriptions must stay in the address bar so it can
 * be copied back out on mobile. Cloudflare edge rules (infrastructure/cloudflare/rulesets.py)
 * rewrite `/` to the static /en/requests/ page and redirect every other page path to gen.
 *
 * Usage:
 *   npx playwright test requests-vanity-host.spec.ts --config=playwright.production.config.ts
 */

const REQUESTS_ORIGIN = 'https://requests.nomadkaraoke.com';

test.describe('requests.nomadkaraoke.com vanity host', () => {
  test('serves the board at / without redirecting away', async ({ page }) => {
    // The board's data call to the API must succeed from this origin.
    const boardResponse = page.waitForResponse(
      (r) => r.url().includes('/api/requests-board/') && r.request().method() === 'GET',
      { timeout: 30_000 },
    );
    const response = await page.goto(`${REQUESTS_ORIGIN}/?utm_source=e2e`);
    expect(response?.status()).toBe(200);
    // No redirect chain: the first response is the page itself.
    expect(response?.request().redirectedFrom()).toBeNull();

    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 30_000 });
    expect((await boardResponse).ok()).toBe(true);

    const url = new URL(page.url());
    expect(url.host).toBe('requests.nomadkaraoke.com');
    expect(url.pathname).toBe('/');
    expect(url.searchParams.get('utm_source')).toBe('e2e');
  });

  test('is not treated as a white-label tenant', async ({ page }) => {
    const tenantHeaders: string[] = [];
    page.on('request', (req) => {
      const tenant = req.headers()['x-tenant-id'];
      if (tenant) tenantHeaders.push(tenant);
    });
    await page.goto(`${REQUESTS_ORIGIN}/`);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 30_000 });
    expect(tenantHeaders).toEqual([]);
  });

  test('other page paths redirect to gen', async ({ request }) => {
    const response = await request.get(`${REQUESTS_ORIGIN}/en/app/`, { maxRedirects: 0 });
    expect([301, 302]).toContain(response.status());
    expect(response.headers()['location']).toBe('https://gen.nomadkaraoke.com/en/app/');
  });
});
