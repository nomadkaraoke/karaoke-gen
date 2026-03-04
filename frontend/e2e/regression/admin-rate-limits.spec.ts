import { test, expect } from "@playwright/test"
import { setAuthToken } from "../fixtures/test-helper"

/**
 * Admin Rate Limits Page - Smoke Tests
 *
 * Basic smoke tests for the rate limits admin page.
 * These tests verify the page loads and basic navigation works.
 *
 * More detailed functionality is covered by Jest unit tests.
 */

// Mock data for API responses
const mockStats = {
  jobs_per_day_limit: 5,
  rate_limiting_enabled: true,
  youtube_uploads_today: 3,
  youtube_quota_units_consumed: 900,
  youtube_quota_units_remaining: 8600,
  youtube_quota_daily_limit: 10000,
  youtube_quota_effective_limit: 9500,
  youtube_quota_upload_cost: 300,
  youtube_quota_estimated_uploads_remaining: 28,
  youtube_quota_seconds_until_reset: 43200,
  youtube_uploads_queued: 0,
  youtube_uploads_failed: 0,
  gcp_quota_available: false,
  quota_drift_alert: false,
  disposable_domains_count: 130,
  blocked_emails_count: 2,
  blocked_ips_count: 1,
  total_overrides: 1,
}

const mockBlocklists = {
  disposable_domains: ["tempmail.com", "mailinator.com", "guerrillamail.com"],
  blocked_emails: ["spammer@example.com"],
  blocked_ips: ["192.168.1.100"],
  updated_at: "2025-01-09T10:00:00Z",
  updated_by: "admin@nomadkaraoke.com",
}

const mockOverrides = {
  overrides: [
    {
      email: "vip@example.com",
      bypass_job_limit: true,
      custom_daily_job_limit: null,
      reason: "VIP user",
      created_by: "admin@nomadkaraoke.com",
      created_at: "2025-01-08T15:00:00Z",
    },
  ],
  total: 1,
}

const mockUserMe = {
  user: {
    email: "admin@nomadkaraoke.com",
    role: "admin",
    credits: -1,
  },
}

test.describe("Admin Rate Limits Page", () => {
  test.beforeEach(async ({ page }) => {
    // Set auth token for admin access
    await setAuthToken(page, "test-admin-token")

    // Mock all API routes that the rate limits page needs
    await page.route("**/api/users/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockUserMe),
      })
    })

    await page.route("**/api/admin/rate-limits/stats", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockStats),
      })
    })

    await page.route("**/api/admin/rate-limits/blocklists", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockBlocklists),
      })
    })

    await page.route("**/api/admin/rate-limits/overrides", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockOverrides),
      })
    })

    await page.route("**/api/admin/youtube-queue**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ entries: [], total: 0 }),
      })
    })

    await page.route("**/api/admin/stats/overview", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_users: 100,
          total_jobs: 500,
          active_users: 50,
          jobs_by_status: {},
        }),
      })
    })

    // Mock any other admin API calls with a generic success response
    await page.route("**/api/admin/**", async (route) => {
      if (
        !route.request().url().includes("rate-limits") &&
        !route.request().url().includes("stats")
      ) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ success: true }),
        })
      } else {
        await route.continue()
      }
    })
  })

  test("rate limits page loads and shows title", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Check page title
    await expect(page.locator("h1")).toContainText("Rate Limits")
    await expect(
      page.getByText("Manage rate limiting, blocklists, and user overrides")
    ).toBeVisible()
  })

  test("all three tabs are visible", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Check all tabs are present
    await expect(page.getByRole("tab", { name: /overview/i })).toBeVisible()
    await expect(page.getByRole("tab", { name: /blocklists/i })).toBeVisible()
    await expect(page.getByRole("tab", { name: /user overrides/i })).toBeVisible()
  })

  test("can switch between tabs", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Overview is default
    await expect(page.getByRole("tab", { name: /overview/i })).toHaveAttribute(
      "data-state",
      "active"
    )

    // Click blocklists tab
    await page.getByRole("tab", { name: /blocklists/i }).click()
    await expect(page.getByRole("tab", { name: /blocklists/i })).toHaveAttribute(
      "data-state",
      "active"
    )

    // Click user overrides tab
    await page.getByRole("tab", { name: /user overrides/i }).click()
    await expect(page.getByRole("tab", { name: /user overrides/i })).toHaveAttribute(
      "data-state",
      "active"
    )
  })

  test("refresh button is visible and clickable", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Check refresh button exists and click it
    const refreshButton = page.getByRole("button", { name: /refresh/i })
    await expect(refreshButton).toBeVisible()
    await refreshButton.click()

    // Should not throw error
    await page.waitForLoadState("networkidle")
  })

  test("shows add override button in overrides tab", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Navigate to overrides tab
    await page.getByRole("tab", { name: /user overrides/i }).click()

    // Check add override button is visible
    await expect(page.getByRole("button", { name: /add override/i })).toBeVisible()
  })

  test("add override dialog opens and closes", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Navigate to overrides tab
    await page.getByRole("tab", { name: /user overrides/i }).click()

    // Open dialog
    await page.getByRole("button", { name: /add override/i }).click()

    // Check dialog is visible
    await expect(page.getByText("Add User Override")).toBeVisible()
    await expect(page.getByPlaceholder("user@example.com")).toBeVisible()

    // Close dialog by clicking cancel
    await page.getByRole("button", { name: /cancel/i }).click()

    // Dialog should be closed
    await expect(page.getByText("Add User Override")).not.toBeVisible()
  })

  test("blocklists tab shows domain search input", async ({ page }) => {
    await page.goto("/admin/rate-limits")
    await page.waitForLoadState("networkidle")

    // Navigate to blocklists tab
    await page.getByRole("tab", { name: /blocklists/i }).click()

    // Check search input is visible
    await expect(page.getByPlaceholder("Search domains...")).toBeVisible()
    await expect(page.getByPlaceholder("Search emails...")).toBeVisible()
    await expect(page.getByPlaceholder("Search IPs...")).toBeVisible()
  })
})
