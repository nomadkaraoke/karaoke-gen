import { defineConfig, devices } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// Load .env.local file if it exists
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
 * Playwright configuration for DEMO VIDEO recording.
 *
 * Records a high-resolution (1280x720) video of the happy path flow
 * against production. The resulting video is post-processed with ffmpeg
 * to create a polished demo video.
 *
 * Usage:
 *   E2E_TEST_TOKEN=<token> npx playwright test --config=playwright.demo.config.ts
 *   npm run test:e2e:demo
 */
export default defineConfig({
  testDir: './e2e/production',
  testMatch: 'demo-recording.spec.ts',

  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0, // No retries - we want a clean single recording
  workers: 1,

  reporter: [
    ['html', { open: 'never', outputFolder: 'playwright-report-demo' }],
    ['list'],
  ],

  use: {
    baseURL: 'https://gen.nomadkaraoke.com',

    // High resolution video recording
    video: {
      mode: 'on',
      size: { width: 1280, height: 720 },
    },

    // Capture screenshots at each step
    screenshot: 'on',

    // Collect trace for debugging
    trace: 'on',

    // Standard timeouts
    actionTimeout: 60000,

    // Viewport matches video size
    viewport: { width: 1280, height: 720 },
  },

  // 40 minutes for the full pipeline
  timeout: 2400000,

  expect: {
    timeout: 60000,
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 720 },
      },
    },
  ],
});
