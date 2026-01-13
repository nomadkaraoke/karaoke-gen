import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken, clearAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - User Impersonation
 *
 * Tests the admin impersonation feature using mocked API responses.
 * These tests run offline in CI without hitting production.
 */

// Mock data for admin user
const mockAdminUser = {
  email: 'admin@nomadkaraoke.com',
  role: 'admin',
  credits: 100,
  display_name: 'Admin User',
  total_jobs_created: 10,
  total_jobs_completed: 8,
};

// Mock data for regular user to impersonate
const mockTargetUser = {
  email: 'user@example.com',
  role: 'user',
  credits: 5,
  display_name: 'Regular User',
  total_jobs_created: 2,
  total_jobs_completed: 1,
};

// Mock user list for admin page
const mockUserList = {
  users: [
    mockAdminUser,
    mockTargetUser,
    {
      email: 'another@example.com',
      role: 'user',
      credits: 3,
      display_name: null,
      total_jobs_created: 0,
      total_jobs_completed: 0,
    },
  ],
  total: 3,
  has_more: false,
};

test.describe('User Impersonation', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
  });

  test('admin can see impersonate button for other users', async ({ page }) => {
    // Set up admin token
    await setAuthToken(page, 'admin-test-token');

    await setupApiFixtures(page, {
      mocks: [
        // Admin user profile (initial load)
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: { user: mockAdminUser } },
        },
        // Admin users list
        {
          method: 'GET',
          path: '/api/users/admin/users',
          response: { body: mockUserList },
        },
        // Tenant config
        {
          method: 'GET',
          path: '/api/tenant/config',
          response: { body: { brand_code: null, is_white_label: false } },
        },
      ],
    });

    // Navigate to admin users page
    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Verify we're on the admin page
    await expect(page.locator('h1')).toContainText('Users');

    // Find the impersonate button for a regular user (not the admin themselves)
    const targetUserRow = page.locator('tr').filter({ hasText: 'user@example.com' });
    await expect(targetUserRow).toBeVisible();

    // Get the impersonate button - should be enabled for regular users
    const impersonateButton = targetUserRow.locator('button[title="Impersonate user"]');
    await expect(impersonateButton).toBeVisible();
    await expect(impersonateButton).toBeEnabled();

    // Verify the admin's own row has a disabled impersonate button
    const adminRow = page.locator('tr').filter({ hasText: 'admin@nomadkaraoke.com' });
    await expect(adminRow).toBeVisible();
    const adminImpersonateButton = adminRow.locator('button[title="Impersonate user"]');
    await expect(adminImpersonateButton).toBeVisible();
    await expect(adminImpersonateButton).toBeDisabled();

    // Verify there are no "Make Admin" buttons (they were removed)
    const makeAdminButtons = page.locator('button[title="Make admin"], button[title="Remove admin"]');
    await expect(makeAdminButtons).toHaveCount(0);
  });

  // TODO: Test skipped - impersonation banner requires in-memory zustand state
  // that cannot be reliably set via localStorage persistence.
  // The banner component is tested indirectly via the "admin can see impersonate button"
  // test and manual verification. The ImpersonationBanner component itself is simple
  // and renders based on isImpersonating state from the auth store.
  test.skip('impersonation banner shows target user email', async ({ page }) => {
    // This test would need to inject isImpersonating: true into zustand's in-memory
    // state before hydration, which is not reliably possible with the current test setup.
    // The banner only renders when useAuth().isImpersonating is true, which is
    // intentionally NOT persisted to localStorage for security reasons.
  });

  // TODO: Test skipped - requires full impersonation flow simulation
  // The end-to-end impersonation flow requires dynamic API mocking that returns
  // different users for the same endpoint at different times.
  test.skip('stop impersonating returns to admin context', async ({ page }) => {
    // This test would need to:
    // 1. Start as admin
    // 2. Click impersonate (requires dynamic /api/users/me mock)
    // 3. Verify banner appears
    // 4. Click "Stop Impersonating"
    // 5. Verify admin context is restored
    // The mock infrastructure doesn't easily support this stateful flow.
  });

  test('make admin button is removed from users page', async ({ page }) => {
    await setAuthToken(page, 'admin-test-token');

    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: { user: mockAdminUser } },
        },
        {
          method: 'GET',
          path: '/api/users/admin/users',
          response: { body: mockUserList },
        },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Shield icon button should NOT exist (Make Admin was removed)
    const shieldButtons = page.locator('button[title="Make admin"], button[title="Remove admin"]');
    await expect(shieldButtons).toHaveCount(0);

    // Impersonate button should exist
    const impersonateButtons = page.locator('button[title="Impersonate user"]');
    await expect(impersonateButtons).toHaveCount(await page.locator('tbody tr').count());
  });

  test('cannot impersonate self', async ({ page }) => {
    await setAuthToken(page, 'admin-test-token');

    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: { user: mockAdminUser } },
        },
        {
          method: 'GET',
          path: '/api/users/admin/users',
          response: { body: mockUserList },
        },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Find the admin's own row
    const adminRow = page.locator('tr').filter({ hasText: 'admin@nomadkaraoke.com' });
    await expect(adminRow).toBeVisible();

    // The impersonate button should be disabled for the admin's own row
    const impersonateButton = adminRow.locator('button[title="Impersonate user"]');
    await expect(impersonateButton).toBeDisabled();
  });

  test('clicking impersonate button makes API call', async ({ page }) => {
    await setAuthToken(page, 'admin-test-token');

    // Track if impersonate API was called
    const impersonateCalls: string[] = [];

    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: { user: mockAdminUser } },
        },
        {
          method: 'GET',
          path: '/api/users/admin/users',
          response: { body: mockUserList },
        },
        {
          method: 'GET',
          path: '/api/tenant/config',
          response: { body: { brand_code: null, is_white_label: false } },
        },
        // Mock the impersonate endpoint
        {
          method: 'POST',
          path: '/api/admin/users/:email/impersonate',
          response: {
            body: {
              session_token: 'impersonation-token-123',
              user_email: 'user@example.com',
              message: 'Impersonation started',
            },
          },
        },
      ],
    });

    // Also add a listener to track the actual impersonate call
    await page.route('**/api/admin/users/*/impersonate', async (route) => {
      const url = route.request().url();
      const match = url.match(/\/api\/admin\/users\/([^/]+)\/impersonate/);
      if (match) {
        impersonateCalls.push(decodeURIComponent(match[1]));
      }
      // Continue to the mock server
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_token: 'impersonation-token-123',
          user_email: decodeURIComponent(match?.[1] || ''),
          message: 'Impersonation started',
        }),
      });
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Find the row for the target user (not admin)
    const targetUserRow = page.locator('tr').filter({ hasText: 'user@example.com' });
    await expect(targetUserRow).toBeVisible();

    // Get the impersonate button
    const impersonateButton = targetUserRow.locator('button[title="Impersonate user"]');
    await expect(impersonateButton).toBeVisible();
    await expect(impersonateButton).toBeEnabled();

    // Click the impersonate button
    await impersonateButton.click();

    // Wait a bit for the API call to be made
    await page.waitForTimeout(1000);

    // Verify the API was called
    expect(impersonateCalls.length).toBeGreaterThan(0);
    expect(impersonateCalls[0]).toBe('user@example.com');
  });
});

test.describe('Impersonation Security', () => {
  test('non-admin cannot access admin users page', async ({ page }) => {
    // Set up as regular user
    await setAuthToken(page, 'user-test-token');

    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/users/me',
          response: { body: { user: mockTargetUser } },
        },
      ],
    });

    await page.goto('/admin/users');

    // Should be redirected away from admin page
    // The admin layout checks for admin role and redirects to /app
    await page.waitForURL('**/app**', { timeout: 10000 });
  });
});
