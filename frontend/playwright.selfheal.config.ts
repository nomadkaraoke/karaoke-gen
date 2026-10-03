import { defineConfig, devices } from '@playwright/test';

/**
 * Offline tests for the self-healing Stripe Checkout runner
 * (e2e/helpers/self-healing). Fixture pages are served via page.route on fake
 * checkout.stripe.com / js.stripe.com URLs and the LLM is a stub — no network,
 * no dev server, no real Stripe, no Gemini. Runs in CI's frontend-e2e-smoke job.
 *
 *   npx playwright test --config=playwright.selfheal.config.ts
 */
export default defineConfig({
  testDir: './e2e/self-healing',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  timeout: 150_000,
  use: { ...devices['Desktop Chrome'] },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
