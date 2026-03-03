import { test, expect, Page } from '@playwright/test';
import { setupApiFixtures, setAuthToken, clearAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Mobile Responsiveness
 *
 * Tests mobile viewport rendering and interactions using mocked API responses.
 * These tests run offline in CI without hitting production.
 */

// Common mobile viewport sizes
const MOBILE_VIEWPORTS = {
  'iPhone SE': { width: 375, height: 667 },
  'iPhone 14': { width: 390, height: 844 },
  'Pixel 7': { width: 412, height: 915 },
} as const;

// Standard mocks for app page tests
const APP_PAGE_MOCKS = [
  {
    method: 'GET',
    path: '/api/jobs',
    response: { body: [] },
  },
  {
    method: 'GET',
    path: '/api/users/me',
    response: {
      body: {
        user: { email: 'test@example.com', credits: 5, role: 'user' },
        has_session: true,
      },
    },
  },
  {
    method: 'GET',
    path: '/api/themes',
    response: { body: { themes: [] } },
  },
  {
    method: 'GET',
    path: '/api/users/credits/packages',
    response: { body: { packages: [] } },
  },
];

// Helper to check no horizontal overflow
async function hasNoHorizontalOverflow(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    const docWidth = document.documentElement.scrollWidth;
    const viewportWidth = window.innerWidth;
    return docWidth <= viewportWidth + 2; // Allow 2px tolerance
  });
}

test.describe('Mobile - Landing Page', () => {
  test.beforeEach(async ({ page }) => {
    await clearAuthToken(page);
  });

  for (const [deviceName, viewport] of Object.entries(MOBILE_VIEWPORTS)) {
    test(`${deviceName}: Landing page loads without horizontal scroll`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, { mocks: [] });

      await page.goto('/');
      await page.waitForLoadState('networkidle');

      const noOverflow = await hasNoHorizontalOverflow(page);
      expect(noOverflow).toBe(true);
    });

    test(`${deviceName}: Hero section is visible`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, { mocks: [] });

      await page.goto('/');
      await page.waitForLoadState('networkidle');

      // Hero should be visible
      await expect(page.locator('h1')).toBeVisible();
      await expect(page.locator('h1')).toContainText('Karaoke');
    });

    test(`${deviceName}: Navigation is accessible`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, { mocks: [] });

      await page.goto('/');
      await page.waitForLoadState('networkidle');

      // Navigation should be visible (might be hamburger menu)
      const nav = page.locator('nav');
      await expect(nav).toBeVisible();
    });

    test(`${deviceName}: Pricing section scrolls into view`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, {
        mocks: [
          {
            method: 'GET',
            path: '/api/users/credits/packages',
            response: {
              body: {
                packages: [
                  { id: 'credit_1', credits: 1, price_cents: 500 },
                ],
              },
            },
          },
        ],
      });

      await page.goto('/');
      await page.waitForLoadState('networkidle');

      await page.locator('#pricing').scrollIntoViewIfNeeded();

      // Pricing section should be in viewport
      await expect(page.locator('#pricing')).toBeInViewport();
    });
  }
});

test.describe('Mobile - App Page', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
  });

  for (const [deviceName, viewport] of Object.entries(MOBILE_VIEWPORTS)) {
    test(`${deviceName}: App page loads without horizontal scroll`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });

      await page.goto('/app');
      await page.waitForLoadState('networkidle');

      const noOverflow = await hasNoHorizontalOverflow(page);
      expect(noOverflow).toBe(true);
    });

    test(`${deviceName}: Song info inputs are visible`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });

      await page.goto('/app');
      await page.waitForLoadState('networkidle');

      // GuidedJobFlow Step 1 inputs should be visible
      const artistInput = page.getByTestId('guided-artist-input');
      await expect(artistInput).toBeVisible();
      await expect(page.getByTestId('guided-title-input')).toBeVisible();
    });

    test(`${deviceName}: Form inputs fit viewport`, async ({ page }) => {
      await page.setViewportSize(viewport);

      await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });

      await page.goto('/app');
      await page.waitForLoadState('networkidle');

      // Input should fit within viewport
      const artistInput = page.getByTestId('guided-artist-input');
      const box = await artistInput.boundingBox();

      if (box) {
        expect(box.width).toBeLessThanOrEqual(viewport.width);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 10);
      }
    });
  }
});

test.describe('Mobile - Touch Targets', () => {
  const MINIMUM_TOUCH_TARGET = 44; // iOS HIG minimum

  test('buttons have adequate touch target size', async ({ page }) => {
    await page.setViewportSize(MOBILE_VIEWPORTS['iPhone SE']);

    await clearAuthToken(page);
    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const buttons = page.locator('button:visible');
    const count = await buttons.count();

    let smallButtonCount = 0;
    for (let i = 0; i < Math.min(count, 10); i++) {
      const button = buttons.nth(i);
      const box = await button.boundingBox();

      if (box && (box.width < 28 || box.height < 28)) {
        smallButtonCount++;
      }
    }

    // Allow some small buttons (icon buttons) but not too many
    expect(smallButtonCount).toBeLessThan(3);
  });

  test('form inputs have adequate height', async ({ page }) => {
    await setAuthToken(page, 'test-token');
    await page.setViewportSize(MOBILE_VIEWPORTS['iPhone SE']);

    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    const artistInput = page.getByTestId('guided-artist-input');
    const box = await artistInput.boundingBox();

    if (box) {
      // Input should be at least 36px tall for easy touch
      expect(box.height).toBeGreaterThanOrEqual(36);
    }
  });
});

test.describe('Mobile - Interactions', () => {
  test.beforeEach(async ({ page }) => {
    await setAuthToken(page, 'test-token');
    await page.setViewportSize(MOBILE_VIEWPORTS['iPhone SE']);
  });

  test('can type in input fields', async ({ page }) => {
    await setupApiFixtures(page, { mocks: APP_PAGE_MOCKS });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    const artistInput = page.getByTestId('guided-artist-input');
    await artistInput.focus();
    await artistInput.fill('Test Artist');

    await expect(artistInput).toHaveValue('Test Artist');
  });

  test('dialogs fit mobile viewport', async ({ page }) => {
    await setupApiFixtures(page, {
      mocks: [
        {
          method: 'GET',
          path: '/api/jobs',
          response: {
            body: [
              {
                job_id: 'job-1',
                artist: 'Test',
                title: 'Song',
                status: 'awaiting_audio_selection',
                audio_options: [{ id: '1', title: 'Audio 1' }],
              },
            ],
          },
        },
        {
          method: 'GET',
          path: '/api/users/me',
          response: {
            body: {
              user: { email: 'test@example.com', credits: 5, role: 'user' },
              has_session: true,
            },
          },
        },
        {
          method: 'GET',
          path: '/api/themes',
          response: { body: { themes: [] } },
        },
        {
          method: 'GET',
          path: '/api/users/credits/packages',
          response: { body: { packages: [] } },
        },
      ],
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    const selectAudioBtn = page.getByRole('button', { name: /select audio/i }).first();
    if (await selectAudioBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await selectAudioBtn.click();

      const dialog = page.locator('[role="dialog"]');
      if (await dialog.isVisible({ timeout: 3000 }).catch(() => false)) {
        const box = await dialog.boundingBox();

        if (box) {
          const viewport = MOBILE_VIEWPORTS['iPhone SE'];
          // Dialog should fit within viewport (with some margin)
          expect(box.width).toBeLessThanOrEqual(viewport.width);
        }
      }
    }
  });

  test('can scroll through content', async ({ page }) => {
    await clearAuthToken(page);

    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Get initial scroll position
    const initialScroll = await page.evaluate(() => window.scrollY);

    // Scroll down
    await page.evaluate(() => window.scrollBy(0, 500));
    await page.waitForTimeout(300);

    // Scroll position should have changed
    const newScroll = await page.evaluate(() => window.scrollY);
    expect(newScroll).toBeGreaterThan(initialScroll);
  });
});

test.describe('Mobile - Accessibility', () => {
  test('has proper viewport meta tag', async ({ page }) => {
    await page.setViewportSize(MOBILE_VIEWPORTS['iPhone SE']);
    await clearAuthToken(page);

    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const viewportMeta = await page.evaluate(() => {
      const meta = document.querySelector('meta[name="viewport"]');
      return meta?.getAttribute('content') || '';
    });

    expect(viewportMeta).toContain('width=device-width');
    expect(viewportMeta).toContain('initial-scale=1');
  });

  test('text is readable without zooming', async ({ page }) => {
    await page.setViewportSize(MOBILE_VIEWPORTS['iPhone SE']);
    await clearAuthToken(page);

    await setupApiFixtures(page, { mocks: [] });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Check body font size is at least 14px
    const fontSize = await page.evaluate(() => {
      const body = document.body;
      const style = window.getComputedStyle(body);
      return parseFloat(style.fontSize);
    });

    expect(fontSize).toBeGreaterThanOrEqual(14);
  });
});
