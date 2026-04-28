import { test, expect, Page } from '@playwright/test'

/**
 * Custom Lyrics LLM Mode (production)
 *
 * Verifies the Custom Lyrics AI mode end-to-end against live production:
 *   1. Lists jobs filtered by status=in_review (admin can see all jobs).
 *   2. Navigates to the lyrics review page for the first matching job.
 *   3. Opens the "Edit All Lyrics" modal, picks "Custom Lyrics".
 *   4. Fills the prompt textarea, clicks "Generate Custom Lyrics".
 *   5. Waits for the preview phase (up to 120s for the LLM call).
 *   6. Asserts the generated lyrics are non-empty.
 *   7. Clicks "Save Custom Lyrics" and verifies the success toast.
 *
 * Skipped if KARAOKE_ADMIN_TOKEN is unset or no in_review jobs exist.
 *
 * Run with:
 *   KARAOKE_ADMIN_TOKEN=xxx npx playwright test \
 *     e2e/production/custom-lyrics-mode.spec.ts \
 *     --config=playwright.production.config.ts --reporter=list
 */

const PROD_URL = 'https://gen.nomadkaraoke.com'
const API_URL = 'https://api.nomadkaraoke.com'

const ADMIN_TOKEN = process.env.KARAOKE_ADMIN_TOKEN

test.describe('Custom Lyrics LLM mode (production)', () => {
  test.skip(!ADMIN_TOKEN, 'KARAOKE_ADMIN_TOKEN not set')

  // Disable retries: we mutate production review state, retrying could double-save.
  test.describe.configure({ retries: 0 })

  test('generate, preview, save', async ({ page }: { page: Page }) => {
    test.setTimeout(180_000) // up to 3 minutes including LLM call

    // Find an existing in_review job to operate on. Admins see all jobs via
    // the standard list endpoint (no separate /api/admin/jobs route exists).
    const listResponse = await page.request.get(
      `${API_URL}/api/jobs?status=in_review&limit=1&fields=summary`,
      { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
    )
    expect(listResponse.ok()).toBe(true)
    const listBody = (await listResponse.json()) as
      | { jobs?: Array<{ id?: string; job_id?: string }> }
      | Array<{ id?: string; job_id?: string }>
    const jobs = Array.isArray(listBody) ? listBody : listBody.jobs ?? []
    test.skip(!jobs.length, 'No in_review jobs available for E2E')
    const jobId = jobs[0].id ?? jobs[0].job_id
    expect(jobId, 'List response did not include a job id').toBeTruthy()

    // Inject token into localStorage (the app's auth pattern).
    // Existing prod E2Es (admin-dashboard, duet-review, etc.) all use this key.
    await page.addInitScript((token: string) => {
      window.localStorage.setItem('karaoke_access_token', token)
    }, ADMIN_TOKEN!)

    // Hash-based routing for cloud-mode review pages.
    await page.goto(`${PROD_URL}/app/jobs/#/${jobId}/review`)

    const editAllBtn = page.getByRole('button', { name: /Edit All Lyrics/i })
    await expect(editAllBtn).toBeVisible({ timeout: 30_000 })
    await editAllBtn.click()

    // The mode-selection modal exposes a "Custom Lyrics" card. Use .first()
    // because the title also appears as a heading after selection.
    await page.getByRole('button', { name: /Custom Lyrics/i }).first().click()

    // Input phase: type a tiny custom-lyrics request.
    await page.getByLabel(/Custom lyrics or instructions/i).fill(
      "Replace 'baby' with 'sweetie' wherever it appears, otherwise keep the original."
    )

    await page.getByRole('button', { name: /Generate Custom Lyrics/i }).click()

    // Wait for preview phase: the Save button appears once generation completes.
    await expect(
      page.getByRole('button', { name: /Save Custom Lyrics/i }),
    ).toBeVisible({ timeout: 120_000 })

    // Capture the generated text and ensure non-empty.
    const previewTextarea = page.getByPlaceholder(
      /Generated lyrics will appear here/,
    )
    const generated = await previewTextarea.inputValue()
    expect(generated.trim().length).toBeGreaterThan(0)

    // Save and verify toast appears (matches lyricsReview.toasts.customLyricsSaved).
    await page.getByRole('button', { name: /Save Custom Lyrics/i }).click()
    await expect(
      page.getByText(/Manually sync each edited segment/i),
    ).toBeVisible({ timeout: 10_000 })
  })
})
