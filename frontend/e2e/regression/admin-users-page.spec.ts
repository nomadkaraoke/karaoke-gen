import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken } from '../fixtures/test-helper';

/**
 * Admin Users Page Regression Tests
 *
 * Tests the admin users list page including new columns (Created, Last Login),
 * sortable headers, and correct navigation links.
 */

const mockAdminUser = {
  user: {
    email: 'admin@nomadkaraoke.com',
    role: 'admin',
    credits: 100,
    is_active: true,
  },
};

const mockUsersList = {
  users: [
    {
      email: 'user1@example.com',
      display_name: 'User One',
      role: 'user',
      credits: 50,
      is_active: true,
      total_jobs_created: 10,
      total_jobs_completed: 8,
      total_spent: 2500,
      created_at: '2025-06-15T10:00:00Z',
      last_login_at: '2026-03-01T14:30:00Z',
    },
    {
      email: 'user2@example.com',
      display_name: null,
      role: 'user',
      credits: 0,
      is_active: true,
      total_jobs_created: 3,
      total_jobs_completed: 2,
      total_spent: 0,
      created_at: '2026-01-20T08:00:00Z',
      last_login_at: null,
    },
    {
      email: 'admin@nomadkaraoke.com',
      display_name: 'Admin User',
      role: 'admin',
      credits: 999,
      is_active: true,
      total_jobs_created: 100,
      total_jobs_completed: 95,
      total_spent: 49500,
      created_at: '2024-01-01T00:00:00Z',
      last_login_at: '2026-03-04T09:00:00Z',
    },
  ],
  total: 3,
  has_more: false,
};

test.describe('Admin Users Page', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'mock-admin-token');
  });

  test('renders all expected table columns', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Check all expected column headers
    await expect(page.getByRole('columnheader', { name: /Email/ })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Role' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: /Credits/ })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Spent' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Jobs' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: /Created/ })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: /Last Login/ })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Actions' })).toBeVisible();
  });

  test('displays created_at and last_login_at as relative time', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // User with no last_login should show "—"
    const user2Row = page.locator('tr').filter({ hasText: 'user2@example.com' });
    await expect(user2Row.locator('td').nth(6)).toContainText('—');

    // Users with dates should show relative time (contains "ago" or "just now")
    const user1Row = page.locator('tr').filter({ hasText: 'user1@example.com' });
    // Created column should have some relative time text
    await expect(user1Row.locator('td').nth(5)).not.toBeEmpty();
  });

  test('clicking Email column header sorts by email', async ({ page }) => {
    const requestUrls: string[] = [];

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    // Track requests after fixtures are set up
    page.on('request', (req) => {
      if (req.url().includes('/api/users/admin/users')) {
        requestUrls.push(req.url());
      }
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Click Email header
    await page.getByRole('columnheader', { name: /Email/ }).click();
    await page.waitForLoadState('networkidle');

    // The last request should have sort_by=email
    const lastUrl = requestUrls[requestUrls.length - 1];
    expect(lastUrl).toContain('sort_by=email');
  });

  test('clicking Credits column header sorts by credits', async ({ page }) => {
    const requestUrls: string[] = [];

    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    page.on('request', (req) => {
      if (req.url().includes('/api/users/admin/users')) {
        requestUrls.push(req.url());
      }
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Click Credits header
    await page.getByRole('columnheader', { name: /Credits/ }).click();
    await page.waitForLoadState('networkidle');

    const lastUrl = requestUrls[requestUrls.length - 1];
    expect(lastUrl).toContain('sort_by=credits');
  });

  test('clicking user row navigates to user detail', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // Click the first user row
    await page.locator('tr').filter({ hasText: 'user1@example.com' }).click();

    // Should navigate to user detail page
    await expect(page).toHaveURL(/\/admin\/users\/detail\/?\?email=user1%40example\.com/);
  });

  test('user rows have title attribute for accessibility', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // All data rows should have title="View user details"
    const rows = page.locator('tbody tr[title="View user details"]');
    await expect(rows).toHaveCount(3);
  });

  test('displays user job counts as created/completed', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: mockAdminUser } },
        { method: 'GET', path: '/api/users/admin/users', response: { body: mockUsersList } },
      ],
    });

    await page.goto('/admin/users');
    await page.waitForLoadState('networkidle');

    // User1: 10 created / 8 completed
    const user1Row = page.locator('tr').filter({ hasText: 'user1@example.com' });
    await expect(user1Row).toContainText('10 / 8');
  });
});
