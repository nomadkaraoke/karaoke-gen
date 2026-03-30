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

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Hero section visible
    await expect(page.locator('h1')).toContainText('Karaoke');
    await expect(page.locator('nav')).toBeVisible();
  });

  test('sign in button is visible and clickable', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

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

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('1 Free Credit', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /sign up free/i })).toBeVisible();
  });
});
