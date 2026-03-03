import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Credit Enforcement
 *
 * Tests that the credit system properly gates job creation:
 * - Zero-credit users see warning and disabled submit
 * - 402 errors display "out of credits" message with buy button
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
    await expect(page.getByRole('button', { name: 'Buy Credits' })).toBeVisible();

    // "Choose Audio" button should be disabled (GuidedJobFlow step 1)
    const submitButton = page.getByRole('button', { name: /Choose Audio/i });
    await expect(submitButton).toBeDisabled();
  });

  test('402 error displays out of credits message with buy button', async ({ page }) => {
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
          path: '/api/audio-search/search-standalone',
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

    // Fill in the search form (GuidedJobFlow uses guided-artist/title-input)
    await page.getByTestId('guided-artist-input').fill('Test Artist');
    await page.getByTestId('guided-title-input').fill('Test Song');

    // Submit step 1 to trigger search
    await page.getByRole('button', { name: /Choose Audio/i }).click();

    // Should show credit error message
    await expect(page.getByText(/out of credits/i)).toBeVisible();

    // Should show buy credits button (not a link)
    await expect(page.getByRole('button', { name: 'Buy Credits' })).toBeVisible();
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

    // "Choose Audio" button should be enabled (disabled only when fields empty + no credits)
    // Fill in required fields first
    await page.getByTestId('guided-artist-input').fill('Test Artist');
    await page.getByTestId('guided-title-input').fill('Test Song');

    const submitButton = page.getByRole('button', { name: /Choose Audio/i });
    await expect(submitButton).toBeEnabled();
  });
});
