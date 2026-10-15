import { defineConfig, devices } from '@playwright/test';

/**
 * Full-journey E2E against the gen LOCAL STACK: the real backend on the
 * Firestore + GCS emulators with fake workers (scripts/local-stack.sh). Unlike
 * e2e/regression (static per-test API mocks), every request here hits real
 * routes, and jobs move through their real states in seconds.
 *
 *   npm run test:e2e:local-stack          # starts the stack if it isn't up
 *   scripts/local-stack.sh up --frontend  # or keep it running between runs
 *
 * Ports: LOCAL_STACK_API_PORT (8010), LOCAL_STACK_WEB_PORT (3010).
 * See docs/TESTING.md § "Local stack".
 */
const API_PORT = process.env.LOCAL_STACK_API_PORT || '8010';
const WEB_PORT = process.env.LOCAL_STACK_WEB_PORT || '3010';
const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;

process.env.LOCAL_STACK_API_URL = process.env.LOCAL_STACK_API_URL || API_URL;

export default defineConfig({
  testDir: './e2e/local-stack',
  // Every test creates its own user, and fake-worker knobs are per user, so
  // tests are independent. One worker keeps the Next dev server's first-compile
  // cost from timing out parallel tests; raise it once the pages are warm.
  fullyParallel: true,
  workers: process.env.LOCAL_STACK_WORKERS ? Number(process.env.LOCAL_STACK_WORKERS) : 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['html', { open: 'never', outputFolder: 'playwright-report-local-stack' }], ['list']],
  timeout: 180_000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: WEB_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 30_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: '../scripts/local-stack.sh serve',
      url: `${API_URL}/api/test/status`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
    {
      command: `npx next dev -p ${WEB_PORT} -H 127.0.0.1`,
      url: WEB_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { BACKEND_URL: API_URL },
    },
  ],
});
