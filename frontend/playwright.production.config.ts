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

/**
 * Playwright configuration for PRODUCTION E2E tests.
 *
 * Tests run directly against gen.nomadkaraoke.com (no local dev server).
 * Used for testing the real production system end-to-end.
 *
 * Usage:
 *   npx playwright test --config=playwright.production.config.ts
 *   npm run test:e2e:prod
 */
export default defineConfig({
  testDir: './e2e/production',
  testMatch: '**/*.spec.ts',

  // Run tests sequentially - we're testing real production
  fullyParallel: false,

  // Fail the build on CI if you accidentally left test.only in the source code
  forbidOnly: !!process.env.CI,

  // Retry failed tests in production (flakiness can happen)
  retries: 2,

  // Single worker - we're testing against real production services
  workers: 1,

  // Reporter to use
  reporter: [
    ['html', { open: 'never', outputFolder: 'playwright-report-prod' }],
    ['list'],
  ],

  // Shared settings for all tests
  use: {
    // Base URL for production
    baseURL: 'https://gen.nomadkaraoke.com',

    // Collect trace always for debugging production issues
    trace: 'on',

    // Always capture screenshots
    screenshot: 'on',

    // Always capture video for production debugging
    video: 'on',

    // Longer timeout for production API calls
    actionTimeout: 60000,

    // Log network requests
    extraHTTPHeaders: {
      'x-playwright-test': 'production',
    },
  },

  // Much longer timeout for production tests (karaoke generation takes time!)
  // Remote downloads can take 15+ minutes, plus processing and video generation
  timeout: 2400000, // 40 minutes per test

  // Expect timeout for assertions
  expect: {
    timeout: 60000, // 60 seconds for expects
  },

  // Configure projects (browsers)
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // No webServer needed - we're testing production directly
});
