import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Credit Enforcement
 *
 * Tests that the credit system properly gates job creation:
 * - Zero-credit users see warning and disabled submit
 * - 402 errors display "out of credits" message with buy link
 * - Users with credits can submit normally
 */

const BASE_MOCKS = [
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

function userMeMock(credits: number, role: string = 'user') {
  return {
    method: 'GET',
    path: '/api/users/me',
    response: {
      body: {
        user: { email: 'test@example.com', credits, role },
        has_session: true,
      },
    },
  };
}

test.describe('Credit Enforcement', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  test('zero-credit user sees warning banner and disabled submit', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        userMeMock(0),
        ...BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [] },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Warning banner should be visible
    await expect(page.getByText('You have no credits remaining')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Buy Credits' })).toBeVisible();

    // Submit button should be disabled
    const submitButton = page.getByRole('button', { name: /Create Karaoke Video|Search & Create Job/i });
    await expect(submitButton).toBeDisabled();
  });

  test('402 error displays out of credits message with buy link', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        userMeMock(1),  // Has credits initially (form enabled)
        ...BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [] },
        },
        {
          method: 'POST',
          path: '/api/audio-search',
          response: {
            status: 402,
            body: {
              detail: "You're out of credits. Buy more to continue creating karaoke videos.",
              credits_available: 0,
              credits_required: 1,
              buy_url: '/#pricing',
            },
          },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Fill in the search form
    await page.getByTestId('search-artist-input').fill('Test Artist');
    await page.getByTestId('search-title-input').fill('Test Song');

    // Submit
    await page.getByRole('button', { name: /Search & Create Job/i }).click();

    // Should show credit error message
    await expect(page.getByText(/out of credits/i)).toBeVisible();

    // Should show buy credits link
    await expect(page.getByRole('link', { name: 'Buy Credits' })).toBeVisible();
  });

  test('user with credits can submit normally', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        userMeMock(5),
        ...BASE_MOCKS,
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [] },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // No warning banner
    await expect(page.getByText('You have no credits remaining')).not.toBeVisible();

    // Submit button should be enabled
    const submitButton = page.getByRole('button', { name: /Create Karaoke Video|Search & Create Job/i });
    await expect(submitButton).toBeEnabled();
  });
});
