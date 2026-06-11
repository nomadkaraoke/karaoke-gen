import { test, expect, Page } from '@playwright/test'

/**
 * AI Auto-Correct Suggestions (production)
 *
 * Verifies the opt-in AI suggestions flow end-to-end against live production:
 *   1. Lists jobs filtered by status=in_review (admin can see all jobs).
 *   2. Navigates to the lyrics review page for the first matching job.
 *   3. Opens the "AI Suggest" modal and runs suggestion generation.
 *   4. Waits for the suggestions panel (up to 120s for the LLM call).
 *   5. Asserts the panel renders with accept/reject affordances (or the
 *      explicit "no suggestions" state), then dismisses it WITHOUT
 *      accepting — production review state is never mutated.
 *
 * Skipped if KARAOKE_ADMIN_TOKEN is unset or no in_review jobs exist.
 *
 * Run with:
 *   KARAOKE_ADMIN_TOKEN=xxx npx playwright test \
 *     e2e/production/auto-correct-suggestions.spec.ts \
 *     --config=playwright.production.config.ts --reporter=list
 */

const PROD_URL = 'https://gen.nomadkaraoke.com'
const API_URL = 'https://api.nomadkaraoke.com'

const ADMIN_TOKEN = process.env.KARAOKE_ADMIN_TOKEN

async function openReviewPage(page: Page): Promise<string> {
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

  await page.addInitScript((token: string) => {
    window.localStorage.setItem('karaoke_access_token', token)
  }, ADMIN_TOKEN!)

  await page.goto(`${PROD_URL}/app/jobs/#/${jobId}/review`)
  return jobId as string
}

test.describe('AI auto-correct suggestions (production)', () => {
  test.skip(!ADMIN_TOKEN, 'KARAOKE_ADMIN_TOKEN not set')

  // No retries: each retry would be another paid LLM call.
  test.describe.configure({ retries: 0 })

  test('run suggestions, review panel, dismiss without applying', async ({
    page,
  }: {
    page: Page
  }) => {
    test.setTimeout(180_000) // includes one LLM call (~15-60s)

    await openReviewPage(page)

    const suggestBtn = page.getByRole('button', { name: /AI Suggest/i })
    await expect(suggestBtn).toBeVisible({ timeout: 30_000 })
    await suggestBtn.click()

    // Settings modal appears with the run button (or the no-references
    // warning, in which case the flow correctly ends here).
    const runBtn = page.getByRole('button', { name: /Get suggestions/i })
    const noRefs = page.getByText(/No reference lyrics are available/i)
    await expect(runBtn.or(noRefs).first()).toBeVisible({ timeout: 10_000 })
    if (await noRefs.isVisible().catch(() => false)) {
      test.info().annotations.push({
        type: 'note',
        description: 'Job had no reference lyrics; verified disabled state.',
      })
      return
    }

    await runBtn.click()

    // Panel appears after the LLM call: either a suggestions summary with
    // Accept all / Reject all, or the explicit no-suggestions state.
    const summary = page.getByText(/suggestions? · .* pending/i)
    const none = page.getByText(/no issues found/i)
    await expect(summary.or(none).first()).toBeVisible({ timeout: 120_000 })

    if (await summary.isVisible().catch(() => false)) {
      await expect(
        page.getByRole('button', { name: /Accept all/i }),
      ).toBeVisible()
      await expect(
        page.getByRole('button', { name: /Reject all/i }),
      ).toBeVisible()
    }

    // Dismiss without accepting anything — leaves review state untouched.
    await page.getByRole('button', { name: /Dismiss suggestions/i }).click()
    await expect(summary.or(none).first()).not.toBeVisible()
  })
})
