import { defineConfig, devices } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// Load .env.local file if it exists (for KARAOKE_ACCESS_TOKEN, TESTMAIL_API_KEY, TESTMAIL_NAMESPACE)
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

// Enable fixture recording mode
process.env.RECORD_FIXTURES = 'true';

/**
 * Playwright configuration for FIXTURE RECORDING mode.
 *
 * This config runs tests against production while capturing API responses
 * as fixtures for later use in offline regression tests.
 *
 * Usage:
 *   npx playwright test --config=playwright.record.config.ts
 *   npm run test:e2e:record
 *
 * After recording:
 *   1. Review recordings with: npm run fixtures:review
 *   2. Approve fixtures to move them to approved/
 *   3. Commit approved fixtures
 */
export default defineConfig({
  testDir: './e2e/production',
  testMatch: '**/*.spec.ts',

  // Run tests sequentially for consistent recordings
  fullyParallel: false,

  // Fail on test.only
  forbidOnly: !!process.env.CI,

  // No retries during recording - we want clean captures
  retries: 0,

  // Single worker for consistent API ordering
  workers: 1,

  // Reporter
  reporter: [
    ['html', { open: 'never', outputFolder: 'playwright-report-record' }],
    ['list'],
  ],

  // Shared settings
  use: {
    // Run against production to capture real responses
    baseURL: 'https://gen.nomadkaraoke.com',

    // Always collect trace for debugging
    trace: 'on',

    // Capture screenshots
    screenshot: 'on',

    // Record video
    video: 'on',

    // Longer timeout for API calls
    actionTimeout: 60000,

    // Mark as recording mode
    extraHTTPHeaders: {
      'x-playwright-test': 'recording',
    },
  },

  // Long timeout for full flow
  timeout: 900000, // 15 minutes

  // Expect timeout
  expect: {
    timeout: 60000,
  },

  // Browser
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // No webServer - using production
});
