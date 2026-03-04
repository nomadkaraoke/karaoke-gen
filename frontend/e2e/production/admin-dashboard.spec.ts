import { test, expect, Page } from "@playwright/test";

/**
 * Admin Dashboard Production Tests
 *
 * Tests the admin dashboard functionality on live production.
 * Requires KARAOKE_ADMIN_TOKEN environment variable with admin privileges.
 * Admin tokens must belong to a user with @nomadkaraoke.com email domain.
 *
 * Run with:
 *   KARAOKE_ADMIN_TOKEN=xxx npx playwright test e2e/production/admin-dashboard.spec.ts --reporter=list
 */

const PROD_URL = "https://gen.nomadkaraoke.com";
const API_URL = "https://api.nomadkaraoke.com";

interface UserResponse {
  user: {
    email: string;
    role: string;
  };
}

async function validateAdminToken(
  token: string
): Promise<{ isAdmin: boolean; email?: string; error?: string }> {
  try {
    const response = await fetch(`${API_URL}/api/users/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (!response.ok) {
      return { isAdmin: false, error: `API returned ${response.status}` };
    }

    const data = (await response.json()) as UserResponse;
    const isAdmin =
      data.user?.role === "admin" ||
      data.user?.email?.endsWith("@nomadkaraoke.com");

    return {
      isAdmin,
      email: data.user?.email,
      error: isAdmin ? undefined : `User ${data.user?.email} has role "${data.user?.role}", not admin`,
    };
  } catch (err) {
    return { isAdmin: false, error: `Failed to validate token: ${err}` };
  }
}

function getAdminToken(): string | null {
  // Prefer KARAOKE_ADMIN_TOKEN, fall back to KARAOKE_ACCESS_TOKEN
  return process.env.KARAOKE_ADMIN_TOKEN || process.env.KARAOKE_ACCESS_TOKEN || null;
}

async function authenticatePage(page: Page, token: string): Promise<void> {
  await page.addInitScript((t) => {
    localStorage.setItem("karaoke_access_token", t);
  }, token);
}

// Check if we have a valid admin token before running tests
let adminToken: string | null = null;
let tokenValidation: { isAdmin: boolean; email?: string; error?: string } | null = null;

test.beforeAll(async () => {
  adminToken = getAdminToken();
  if (adminToken) {
    tokenValidation = await validateAdminToken(adminToken);
    if (!tokenValidation.isAdmin) {
      console.log(`⚠️  Token validation failed: ${tokenValidation.error}`);
      console.log("   Admin tests require a token for a @nomadkaraoke.com user");
      console.log("   Set KARAOKE_ADMIN_TOKEN environment variable with an admin token");
    } else {
      console.log(`✓ Admin token validated for ${tokenValidation.email}`);
    }
  } else {
    console.log("⚠️  No admin token found (KARAOKE_ADMIN_TOKEN or KARAOKE_ACCESS_TOKEN)");
  }
});

test.describe("Admin Dashboard - Production", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!adminToken || !tokenValidation?.isAdmin, "Skipping: No valid admin token available");
    await authenticatePage(page, adminToken!);
  });

  test("admin dashboard loads and shows stats", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin`);

    // Wait for page to load
    await expect(page.locator("h1")).toContainText("Admin Dashboard", {
      timeout: 30000,
    });

    // Check stats cards are present
    await expect(page.getByText("Total Users")).toBeVisible();
    await expect(page.getByText("Total Jobs")).toBeVisible();
    await expect(page.getByText("Active Users")).toBeVisible();

    // Check job status breakdown card is present
    await expect(page.getByText("Jobs by Status")).toBeVisible();
  });

  test("admin sidebar navigation works", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin`);

    // Wait for dashboard to load
    await expect(page.locator("h1")).toContainText("Admin Dashboard", {
      timeout: 30000,
    });

    // Click Users in sidebar
    await page.getByRole("link", { name: /Users/i }).first().click();
    await expect(page).toHaveURL(/\/admin\/users/);
    await expect(page.locator("h1")).toContainText("Users");

    // Click Jobs in sidebar
    await page.getByRole("link", { name: /Jobs/i }).first().click();
    await expect(page).toHaveURL(/\/admin\/jobs/);
    await expect(page.locator("h1")).toContainText("Jobs");

    // Navigate back to dashboard
    await page.getByRole("link", { name: /Dashboard/i }).first().click();
    await expect(page).toHaveURL(/\/admin$/);
  });

  test("users page loads with user list", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin/users`);

    // Wait for page to load
    await expect(page.locator("h1")).toContainText("Users", { timeout: 30000 });

    // Check table headers are present
    await expect(page.getByRole("columnheader", { name: "Email" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Role" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Credits" })).toBeVisible();

    // Check search input is present
    await expect(page.getByPlaceholder(/Search/i)).toBeVisible();

    // Check pagination controls
    await expect(page.getByText(/Showing/)).toBeVisible();
  });

  test("jobs page loads with job list", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin/jobs`);

    // Wait for page to load
    await expect(page.locator("h1")).toContainText("Jobs", { timeout: 30000 });

    // Check table headers are present
    await expect(page.getByRole("columnheader", { name: "Job ID" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Status" })).toBeVisible();

    // Check filter controls
    await expect(page.getByPlaceholder(/Filter by user/i)).toBeVisible();
  });

});

test.describe("Admin API - Production", () => {
  test.beforeEach(async () => {
    test.skip(!adminToken || !tokenValidation?.isAdmin, "Skipping: No valid admin token available");
  });

  test("API stats endpoint returns data", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/admin/stats/overview`, {
      headers: {
        Authorization: `Bearer ${adminToken}`,
      },
    });

    expect(response.status()).toBe(200);

    const data = await response.json();
    expect(data).toHaveProperty("total_users");
    expect(data).toHaveProperty("total_jobs");
    expect(data).toHaveProperty("jobs_by_status");
    expect(typeof data.total_users).toBe("number");
    expect(typeof data.total_jobs).toBe("number");

    console.log(`Stats: ${data.total_users} users, ${data.total_jobs} jobs`);
  });

  test("API users list endpoint returns data", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/users/admin/users?limit=5`, {
      headers: {
        Authorization: `Bearer ${adminToken}`,
      },
    });

    expect(response.status()).toBe(200);

    const data = await response.json();
    expect(data).toHaveProperty("users");
    expect(data).toHaveProperty("total");
    expect(Array.isArray(data.users)).toBe(true);

    console.log(`Users: ${data.total} total, fetched ${data.users.length}`);
  });
});
