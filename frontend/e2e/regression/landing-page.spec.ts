import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken, clearAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Landing Page
 *
 * Tests the landing page UI elements using mocked API responses.
 * These tests run offline in CI without hitting production.
 */

test.describe('Landing Page', () => {
  test.beforeEach(async ({ page }) => {
    // Ensure user is logged out for landing page tests
    await clearAuthToken(page);
  });

  test('displays hero section correctly', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Check hero content
    await expect(page.locator('h1')).toContainText('Karaoke Video');
    await expect(page.locator('nav')).toBeVisible();
    // Logo is an image with alt text, not visible text
    await expect(page.getByRole('navigation').getByAltText('Nomad Karaoke')).toBeVisible();
  });

  test('displays pricing section with 4 packages', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/credits/packages',
          response: {
            body: {
              packages: [
                { id: 'credit_1', credits: 1, price_cents: 1000 },
                { id: 'credit_3', credits: 3, price_cents: 2400 },
                { id: 'credit_5', credits: 5, price_cents: 3500 },
                { id: 'credit_10', credits: 10, price_cents: 6000 },
              ],
            },
          },
        },
      ],
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.locator('#pricing').scrollIntoViewIfNeeded();

    // Check all 4 credit packages
    await expect(page.getByRole('button', { name: /1\s+credit/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /3\s+credits/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /5\s+credits/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /10\s+credits/i })).toBeVisible();

    // Check "Best Value" badge
    await expect(page.getByText('Best Value')).toBeVisible();
  });

  test('package selection updates checkout form', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/credits/packages',
          response: {
            body: {
              packages: [
                { id: 'credit_1', credits: 1, price_cents: 1000 },
                { id: 'credit_3', credits: 3, price_cents: 2400 },
                { id: 'credit_5', credits: 5, price_cents: 3500 },
                { id: 'credit_10', credits: 10, price_cents: 6000 },
              ],
            },
          },
        },
      ],
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.locator('#pricing').scrollIntoViewIfNeeded();

    // Click on 10 credits
    await page.getByRole('button', { name: /10\s+credits/i }).click();

    // Check summary updated
    const summaryContainer = page.locator('text=Selected package').locator('..');
    await expect(summaryContainer.getByText('10 Credits')).toBeVisible({ timeout: 10000 });
    await expect(summaryContainer.getByText('$30')).toBeVisible();
  });

  test('displays FAQ section', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Scroll to FAQ section
    await page.getByText('Questions?').scrollIntoViewIfNeeded();

    // Check FAQ section exists
    await expect(page.getByText('Questions?')).toBeVisible();
  });

  test('displays free credits callout in pricing section', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('1 Free Credit', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /sign up free/i })).toBeVisible();
  });

  test('sign in button opens auth dialog', async ({ page }) => {
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Click Sign In
    await page.getByRole('button', { name: /sign in/i }).click();

    // Dialog should open
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('Enter your email to receive a sign-in link')).toBeVisible();
    await expect(dialog.locator('input[type="email"]')).toBeVisible();
  });

  test('logged in users see app content', async ({ page }) => {
    await setAuthToken(page, 'test-valid-token');

    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [] },
        },
        {
          method: 'GET',
          path: '/api/themes',
          response: { body: { themes: [] } },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: { email: 'test@example.com', credits: 5 } },
        },
      ],
    });

    // Go directly to /app with auth token
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Should see app content
    await expect(page.locator('h1')).toContainText('Karaoke');
  });

  test('checkout form validates email', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/credits/packages',
          response: {
            body: {
              packages: [
                { id: 'credit_5', credits: 5, price_cents: 3500 },
              ],
            },
          },
        },
      ],
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.locator('#pricing').scrollIntoViewIfNeeded();

    // Email input should be visible
    const emailInput = page.locator('input[type="email"]').first();
    await expect(emailInput).toBeVisible();

    // Enter invalid email
    await emailInput.fill('invalid');

    // Click checkout - should not proceed (browser validation)
    await page.getByRole('button', { name: /continue to payment/i }).click();

    // Page should still be on landing (not redirected)
    expect(page.url()).not.toContain('stripe.com');
  });
});
