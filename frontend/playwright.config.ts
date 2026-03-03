import { defineConfig, devices } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// Load .env.local file if it exists (for KARAOKE_ACCESS_TOKEN)
const envLocalPath = path.join(__dirname, '.env.local');
if (fs.existsSync(envLocalPath)) {
  const envContent = fs.readFileSync(envLocalPath, 'utf-8');
  for (const line of envContent.split('\n')) {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith('#')) {
      const [key, ...valueParts] = trimmed.split('=');
      const value = valueParts.join('=');
      if (key && value && !process.env[key]) {
        process.env[key] = value;
      }
    }
  }
}

// Generate a stable port per worktree directory to avoid cross-worktree conflicts.
// Each worktree gets a unique port in the 3100-3199 range based on a hash of __dirname.
// Override with E2E_PORT env var, e.g.: E2E_PORT=3001 npx playwright test
const DEFAULT_PORT = (() => {
  const dir = __dirname;
  let hash = 0;
  for (let i = 0; i < dir.length; i++) {
    hash = ((hash << 5) - hash + dir.charCodeAt(i)) | 0;
  }
  return 3100 + (Math.abs(hash) % 100); // 3100-3199
})();
const PORT = process.env.E2E_PORT || String(DEFAULT_PORT);
// Use 127.0.0.1 instead of localhost to avoid IPv6 resolution issues on macOS.
// localhost resolves to ::1 (IPv6) first, but Next.js dev server may not accept
// IPv6 connections, causing Playwright's webServer health check to hang.
const BASE_URL = `http://127.0.0.1:${PORT}`;

/**
 * Playwright configuration for karaoke-gen frontend E2E tests.
 *
 * DEFAULT: Runs regression tests with mocked API (CI-safe, offline).
 * Tests run against the local dev server with mocked API responses.
 *
 * Port configuration:
 *   E2E_PORT=3001 npx playwright test  # Run on custom port
 *
 * For production tests: use playwright.production.config.ts
 * For fixture recording: use playwright.record.config.ts
 */
export default defineConfig({
  testDir: './e2e/regression',

  // Run tests in parallel for faster CI
  fullyParallel: true,

  // Fail the build on CI if you accidentally left test.only in the source code
  forbidOnly: !!process.env.CI,

  // Retry on CI only
  retries: process.env.CI ? 2 : 0,

  // Single worker for now to avoid conflicts with real backend
  workers: 1,

  // Reporter to use
  reporter: [
    ['html', { open: 'never' }],
    ['list'],
  ],

  // Shared settings for all tests
  use: {
    // Base URL for the local dev server (configurable via E2E_PORT)
    baseURL: BASE_URL,

    // Collect trace on failure for debugging
    trace: 'on-first-retry',

    // Screenshot on failure
    screenshot: 'only-on-failure',

    // Video recording (set to 'on' to always record, 'on-first-retry' for failures only)
    video: 'on',

    // Increase timeout for real API calls
    actionTimeout: 30000,

    // Log network requests
    extraHTTPHeaders: {
      'x-playwright-test': 'true',
    },
  },

  // Longer timeout for tests hitting real backend
  timeout: 120000, // 2 minutes per test

  // Expect timeout for assertions
  expect: {
    timeout: 30000, // 30 seconds for expects
  },

  // Configure projects (browsers)
  // Regression tests only use Chromium for CI speed
  // Mobile tests set their own viewport sizes within tests
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Run local dev server before starting tests
  webServer: {
    command: `npm run dev -- -p ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
});
