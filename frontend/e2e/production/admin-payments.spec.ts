import { test, expect, Page } from "@playwright/test";

/**
 * Admin Payments Production Tests
 *
 * Tests the admin payments dashboard functionality on live production.
 * Requires KARAOKE_ADMIN_TOKEN environment variable with admin privileges.
 *
 * Run with:
 *   KARAOKE_ADMIN_TOKEN=xxx npx playwright test e2e/production/admin-payments.spec.ts --reporter=list
 */

const PROD_URL = "https://gen.nomadkaraoke.com";
const API_URL = "https://api.nomadkaraoke.com";

function getAdminToken(): string | null {
  return process.env.KARAOKE_ADMIN_TOKEN || process.env.KARAOKE_ACCESS_TOKEN || null;
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
    const data = (await response.json()) as { user: { email: string; role: string } };
    const isAdmin =
      data.user?.role === "admin" || data.user?.email?.endsWith("@nomadkaraoke.com");
    return { isAdmin, email: data.user?.email };
  } catch (err) {
    return { isAdmin: false, error: `Failed to validate token: ${err}` };
  }
}

async function authenticatePage(page: Page, token: string): Promise<void> {
  await page.addInitScript((t) => {
    localStorage.setItem("karaoke_access_token", t);
  }, token);
}

let adminToken: string | null = null;
let tokenValidation: { isAdmin: boolean; email?: string; error?: string } | null = null;

test.beforeAll(async () => {
  adminToken = getAdminToken();
  if (adminToken) {
    tokenValidation = await validateAdminToken(adminToken);
    if (!tokenValidation.isAdmin) {
      console.log(`⚠️  Token validation failed: ${tokenValidation.error}`);
    } else {
      console.log(`✓ Admin token validated for ${tokenValidation.email}`);
    }
  }
});

test.describe("Admin Payments Page - Production", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!adminToken || !tokenValidation?.isAdmin, "Skipping: No valid admin token available");
    await authenticatePage(page, adminToken!);
  });

  test("payments page loads with revenue tab", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin/payments`);

    // Wait for the page heading
    await expect(page.locator("h1")).toContainText("Payments", { timeout: 30000 });

    // Revenue tab should be visible (default tab)
    await expect(page.getByText("Revenue")).toBeVisible();
    await expect(page.getByText("Transactions")).toBeVisible();
    await expect(page.getByText("Payouts")).toBeVisible();
  });

  test("payments page shows revenue stats", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin/payments`);

    await expect(page.locator("h1")).toContainText("Payments", { timeout: 30000 });

    // Check for key revenue metrics
    await expect(page.getByText("Total Revenue")).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("Stripe Fees")).toBeVisible();
    await expect(page.getByText("Net Revenue")).toBeVisible();
  });

  test("sidebar has payments link", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin`);

    await expect(page.locator("h1")).toContainText("Dashboard", { timeout: 30000 });

    // Click Payments in sidebar
    await page.getByRole("link", { name: /Payments/i }).first().click();
    await expect(page).toHaveURL(/\/admin\/payments/);
    await expect(page.locator("h1")).toContainText("Payments");
  });

  test("transactions tab shows table", async ({ page }) => {
    await page.goto(`${PROD_URL}/admin/payments`);

    await expect(page.locator("h1")).toContainText("Payments", { timeout: 30000 });

    // Click transactions tab
    await page.getByRole("tab", { name: /Transactions/i }).click();

    // Should show table or empty state
    const hasTable = await page.getByRole("table").isVisible().catch(() => false);
    const hasEmpty = await page.getByText(/no payments/i).isVisible().catch(() => false);

    expect(hasTable || hasEmpty).toBe(true);
  });
});

test.describe("Admin Payments API - Production", () => {
  test.beforeEach(async () => {
    test.skip(!adminToken || !tokenValidation?.isAdmin, "Skipping: No valid admin token available");
  });

  test("payment summary endpoint returns data", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/admin/payments/summary?days=30`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });

    expect(response.status()).toBe(200);

    const data = await response.json();
    expect(data).toHaveProperty("total_gross");
    expect(data).toHaveProperty("total_fees");
    expect(data).toHaveProperty("total_net");
    expect(data).toHaveProperty("transaction_count");
    expect(typeof data.total_gross).toBe("number");

    console.log(
      `Revenue (30d): $${(data.total_gross / 100).toFixed(2)} gross, ` +
        `${data.transaction_count} transactions`
    );
  });

  test("payment list endpoint returns data", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/admin/payments?limit=5`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });

    expect(response.status()).toBe(200);

    const data = await response.json();
    expect(data).toHaveProperty("payments");
    expect(data).toHaveProperty("total");
    expect(Array.isArray(data.payments)).toBe(true);

    console.log(`Payments: ${data.total} total, fetched ${data.payments.length}`);
  });

  test("balance endpoint returns data", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/admin/payments/balance`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });

    expect(response.status()).toBe(200);

    const data = await response.json();
    expect(data).toHaveProperty("available");
    expect(data).toHaveProperty("pending");
    expect(typeof data.available).toBe("number");
  });

  test("webhook events endpoint returns data", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/admin/payments/webhook-events?limit=5`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });

    expect(response.status()).toBe(200);

    const data = await response.json();
    expect(Array.isArray(data)).toBe(true);
  });
});
