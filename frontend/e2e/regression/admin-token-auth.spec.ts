import { test, expect } from '@playwright/test';
import { setupApiFixtures, clearAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Admin Token Authentication
 *
 * Tests the admin_token URL parameter flow for one-click login
 * from made-for-you order notification emails.
 */

test.describe('Admin Token Authentication', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
  });

  test('admin_token URL parameter triggers auto-login and shows app', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/auth/verify',
          response: {
            status: 200,
            body: {
              session_token: 'session-abc-123',
              user: {
                email: 'madeforyou@nomadkaraoke.com',
                credits: -1,
                role: 'admin',
              },
            },
          },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: {
            status: 200,
            body: {
              user: {
                email: 'madeforyou@nomadkaraoke.com',
                credits: -1,
                role: 'admin',
                display_name: null,
              },
              has_session: true,
            },
          },
        },
        {
          method: 'GET',
          path: '/api/jobs',
          response: { body: [] },
        },
      ],
    });

    // Navigate with admin_token in URL
    await page.goto('/app?admin_token=test-admin-token-123');

    // Wait for the app to finish loading - look for any app content that indicates auth succeeded
    // The empty state shows "No jobs yet" text when authenticated with no jobs
    await expect(page.getByText(/no jobs yet|create one to get started/i)).toBeVisible({ timeout: 15000 });

    // Should stay on /app (not redirect to /)
    expect(page.url()).toContain('/app');

    // URL should be cleaned (token removed)
    expect(page.url()).not.toContain('admin_token');
  });

  test('invalid admin_token redirects to landing page', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/auth/verify',
          response: {
            status: 401,
            body: { detail: 'Invalid or expired token' },
          },
        },
      ],
    });

    await page.goto('/app?admin_token=invalid-token-xyz');

    // Should redirect to landing page
    await page.waitForURL('/', { timeout: 10000 });
    expect(page.url()).not.toContain('/app');
  });

  test('expired admin_token redirects to landing page', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/auth/verify',
          response: {
            status: 401,
            body: { detail: 'Token has expired' },
          },
        },
      ],
    });

    await page.goto('/app?admin_token=expired-token-abc');

    // Should redirect to landing page
    await page.waitForURL('/', { timeout: 10000 });
  });

  test('admin_token login loads job list', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/auth/verify',
          response: {
            status: 200,
            body: {
              session_token: 'persistent-session-token',
              user: {
                email: 'madeforyou@nomadkaraoke.com',
                credits: -1,
                role: 'admin',
              },
            },
          },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: {
            status: 200,
            body: {
              user: {
                email: 'madeforyou@nomadkaraoke.com',
                credits: -1,
                role: 'admin',
              },
              has_session: true,
            },
          },
        },
        {
          method: 'GET',
          path: '/api/jobs',
          response: {
            body: [
              {
                job_id: 'made-for-you-job-123',
                artist: 'Test Artist',
                title: 'Test Song',
                status: 'in_review',
                made_for_you: true,
                created_at: new Date().toISOString(),
              },
            ],
          },
        },
      ],
    });

    // Login via admin_token
    await page.goto('/app?admin_token=test-admin-token-123');

    // Should show jobs list (proving auth persisted)
    await expect(page.getByText('Test Artist')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Test Song')).toBeVisible();
  });
});
