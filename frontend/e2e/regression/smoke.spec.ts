import { test, expect } from '@playwright/test';
import { setupApiFixtures, clearAuthToken } from '../fixtures/test-helper';

/**
 * Smoke Tests - Quick CI validation (~1 min max)
 *
 * These tests run on every PR to catch critical regressions fast.
 * They only test the landing page (no auth required) for reliability.
 *
 * For comprehensive E2E tests including authenticated flows,
 * add the 'e2e' label to the PR.
 */

test.describe('Smoke Tests', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
  });

  test('landing page loads and shows hero', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    // `domcontentloaded` + an explicit element wait instead of `networkidle`:
    // the marketing page keeps background requests in flight (analytics, fonts,
    // prefetch), so `networkidle` can time out (120s) and redden a deploy on a
    // page that actually rendered fine. Waiting on the concrete hero heading
    // proves the page loaded without depending on the network going quiet.
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Hero section visible
    await expect(page.locator('h1')).toContainText('Karaoke');
    await expect(page.locator('nav')).toBeVisible();
  });

  test('sign in button is visible and clickable', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const signInBtn = page.getByRole('button', { name: /sign in/i });
    await expect(signInBtn).toBeVisible();
    await signInBtn.click();

    // Dialog should open
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('input[type="email"]')).toBeVisible();
  });

  test('free credits messaging is visible', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await expect(page.getByText('1 Free Credit', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /sign up free/i })).toBeVisible();
  });
});
