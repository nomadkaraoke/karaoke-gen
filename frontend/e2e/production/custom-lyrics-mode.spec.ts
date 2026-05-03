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
 *   6. Asserts at least one line input is visible and non-empty.
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

/**
 * Finds an in_review job, injects auth, navigates to the review page,
 * opens "Edit All Lyrics", and clicks into "Custom Lyrics" mode.
 *
 * Returns the jobId used, so callers can build assertions around it.
 * Calls `test.skip` if no in_review jobs are available.
 */
async function openCustomLyricsModal(page: Page): Promise<string> {
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

  return jobId as string
}

test.describe('Custom Lyrics LLM mode (production)', () => {
  test.skip(!ADMIN_TOKEN, 'KARAOKE_ADMIN_TOKEN not set')

  // Disable retries: we mutate production review state, retrying could double-save.
  test.describe.configure({ retries: 0 })

  test('generate, preview, save', async ({ page }: { page: Page }) => {
    test.setTimeout(180_000) // up to 3 minutes including LLM call

    await openCustomLyricsModal(page)

    // Input phase: type a tiny custom-lyrics request.
    await page.getByLabel(/Custom lyrics or instructions/i).fill(
      "Replace 'baby' with 'sweetie' wherever it appears, otherwise keep the original."
    )

    await page.getByRole('button', { name: /Generate Custom Lyrics/i }).click()

    // Wait for preview phase: the Save button appears once generation completes.
    await expect(
      page.getByRole('button', { name: /Save Custom Lyrics/i }),
    ).toBeVisible({ timeout: 120_000 })

    // Assert at least one line input is present and non-empty.
    // Task 18 replaced the plain <Textarea> with <CustomLyricsPreview> which
    // renders one <Input aria-label="Line N"> per line instead of a single textarea.
    const lineInputs = page.locator('input[aria-label^="Line "]')
    await expect(lineInputs.first()).toBeVisible()
    const firstLine = await lineInputs.first().inputValue()
    expect(firstLine.trim().length).toBeGreaterThan(0)

    // Save and verify toast appears (matches lyricsReview.toasts.customLyricsSaved).
    await page.getByRole('button', { name: /Save Custom Lyrics/i }).click()
    await expect(
      page.getByText(/Manually sync each edited segment/i),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('opens generation settings panel and toggles allow_reword', async ({ page }: { page: Page }) => {
    test.setTimeout(60_000)

    await openCustomLyricsModal(page)

    // Generation settings has defaultOpen=true (Task 16), so the panel should
    // already be visible. If somehow collapsed, clicking the trigger opens it.
    const allowRewordSwitch = page.getByRole('switch', { name: /Allow rewording/i })

    // If the settings collapsible is not yet open, open it.
    if (!(await allowRewordSwitch.isVisible())) {
      await page.getByRole('button', { name: /Generation settings/i }).click()
    }

    await expect(allowRewordSwitch).toBeVisible({ timeout: 10_000 })

    // Verify initial state is checked (allow_reword defaults to true).
    await expect(allowRewordSwitch).toHaveAttribute('aria-checked', 'true')

    // Toggle off.
    await allowRewordSwitch.click()

    // Verify switched off.
    await expect(allowRewordSwitch).toHaveAttribute('aria-checked', 'false')
  })

  test('sets strictness to Tight and runs Generate', async ({ page }: { page: Page }) => {
    test.setTimeout(180_000) // LLM call included

    await openCustomLyricsModal(page)

    // If the settings collapsible is not yet open, open it.
    const tightBtn = page.getByRole('button', { name: /^Tight$/ })
    if (!(await tightBtn.isVisible())) {
      await page.getByRole('button', { name: /Generation settings/i }).click()
    }

    await expect(tightBtn).toBeVisible({ timeout: 10_000 })

    // Select "Tight" strictness.
    await tightBtn.click()

    // Verify it is pressed (Task 16 a11y: aria-pressed on strictness buttons).
    await expect(tightBtn).toHaveAttribute('aria-pressed', 'true')

    // Fill prompt and generate.
    await page.getByLabel(/Custom lyrics or instructions/i).fill(
      "Replace 'love' with 'joy' wherever it appears."
    )

    await page.getByRole('button', { name: /Generate Custom Lyrics/i }).click()

    // Wait for preview phase.
    await expect(
      page.getByRole('button', { name: /Save Custom Lyrics/i }),
    ).toBeVisible({ timeout: 120_000 })

    // Assert iterations badge is visible — confirms preview phase rendered.
    await expect(page.getByText(/AI iterations:/i)).toBeVisible({ timeout: 10_000 })
  })

  test('generates with fixed_line_count=OFF without error', async ({ page }: { page: Page }) => {
    test.setTimeout(180_000) // LLM call included

    await openCustomLyricsModal(page)

    // If the settings collapsible is not yet open, open it.
    const segmentCountSwitch = page.getByRole('switch', { name: /Maintain original segment count/i })
    if (!(await segmentCountSwitch.isVisible())) {
      await page.getByRole('button', { name: /Generation settings/i }).click()
    }

    await expect(segmentCountSwitch).toBeVisible({ timeout: 10_000 })

    // Disable fixed line count.
    await segmentCountSwitch.click()
    await expect(segmentCountSwitch).toHaveAttribute('aria-checked', 'false')

    // Fill prompt and generate.
    await page.getByLabel(/Custom lyrics or instructions/i).fill(
      "Replace 'night' with 'day' wherever it appears."
    )

    await page.getByRole('button', { name: /Generate Custom Lyrics/i }).click()

    // Wait for preview phase — confirms generation succeeded.
    await expect(
      page.getByRole('button', { name: /Save Custom Lyrics/i }),
    ).toBeVisible({ timeout: 120_000 })

    // Assert iterations badge is visible — confirms preview phase rendered.
    await expect(page.getByText(/AI iterations:/i)).toBeVisible({ timeout: 10_000 })

    // Variable-count banner only appears when the AI returns a different number
    // of lines than expected. This is non-deterministic, so we soft-assert it.
    const banner = page.getByText(/lines passing/i)
    await banner.isVisible().catch(() => {})
    // NOTE: banner visibility is not asserted — it depends on AI output.
  })
})
