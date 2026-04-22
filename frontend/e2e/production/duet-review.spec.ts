import { test, expect } from '@playwright/test'
import { URLS } from '../helpers/constants'

/**
 * Duet Review — Production E2E Test
 *
 * Happy path: enable duet mode in the lyrics review, assign Singer 2 to the first
 * segment, save corrections, proceed through instrumental review, and verify that
 * the final POST to /api/review/{jobId}/complete includes is_duet=true and the
 * expected segment singer.
 *
 * Prerequisites
 * ─────────────
 *   - A job that is currently in "in_review" state (status = "in_review").
 *   - Either a review token (no login required) or a user access token.
 *
 * Required environment variables
 * ──────────────────────────────
 *   E2E_JOB_ID        Job ID of a job in the "in_review" state.
 *   E2E_REVIEW_TOKEN  (optional) Token embedded in the magic review link.
 *   E2E_TEST_TOKEN    (optional) User access token set in localStorage.
 *                     At least one of E2E_REVIEW_TOKEN or E2E_TEST_TOKEN must be set.
 *
 * Run with:
 *   E2E_JOB_ID=xxx E2E_REVIEW_TOKEN=yyy \
 *     npx playwright test e2e/production/duet-review.spec.ts \
 *       --config=playwright.production.config.ts
 *
 * Or with a user token:
 *   E2E_JOB_ID=xxx E2E_TEST_TOKEN=yyy \
 *     npx playwright test e2e/production/duet-review.spec.ts \
 *       --config=playwright.production.config.ts
 *
 * Notes
 * ─────
 * This test does NOT run in normal CI — it requires a live job in review state and
 * real credentials. It is intended to be run manually after deployment or in a
 * dedicated staging environment.
 *
 * The test verifies the full duet payload round-trip:
 *   lyrics review (toggle duet + assign singer) → instrumental review (submit) →
 *   POST /api/review/{jobId}/complete { is_duet: true, corrected_segments[0].singer: 2 }
 */

const PROD_URL = URLS.production.frontend
const API_URL = URLS.production.api

function requireEnv(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} environment variable is required`)
  return value
}

function getJobId(): string {
  return requireEnv('E2E_JOB_ID')
}

function getReviewToken(): string | null {
  return process.env.E2E_REVIEW_TOKEN || null
}

function getAccessToken(): string | null {
  return process.env.E2E_TEST_TOKEN || null
}

/** Build the lyrics-review page URL for the given job. */
function buildReviewUrl(jobId: string, reviewToken: string | null): string {
  const baseApiUrl = `${API_URL}/api/review/${jobId}`
  if (reviewToken) {
    return `${PROD_URL}/lyrics//?baseApiUrl=${encodeURIComponent(baseApiUrl)}&reviewToken=${reviewToken}`
  }
  return `${PROD_URL}/lyrics//?baseApiUrl=${encodeURIComponent(baseApiUrl)}`
}

test.describe('Lyrics review — duet mode', () => {
  // These tests require explicit env var setup; skip gracefully when not provided.
  test.skip(
    !process.env.E2E_JOB_ID,
    'Set E2E_JOB_ID (and E2E_REVIEW_TOKEN or E2E_TEST_TOKEN) to run this test'
  )

  // Disable retries: we're writing to production state and retrying could double-submit.
  test.describe.configure({ retries: 0 })

  test('toggles duet mode, assigns singer to first segment, verifies payload on save', async ({
    page,
  }) => {
    // Allow enough time for the lyrics review to load and save.
    // The preview video generation step is skipped here (we intercept the save earlier).
    test.setTimeout(120_000)

    const jobId = getJobId()
    const reviewToken = getReviewToken()
    const accessToken = getAccessToken()

    if (!reviewToken && !accessToken) {
      throw new Error(
        'Either E2E_REVIEW_TOKEN or E2E_TEST_TOKEN must be set to authenticate the review page'
      )
    }

    console.log('─────────────────────────────────────────')
    console.log('DUET E2E: Lyrics review — duet mode')
    console.log('─────────────────────────────────────────')
    console.log(`Job ID     : ${jobId}`)
    console.log(`Auth mode  : ${reviewToken ? 'reviewToken' : 'accessToken'}`)
    console.log('')

    // If using a user access token, inject it into localStorage BEFORE navigation.
    if (accessToken && !reviewToken) {
      await page.addInitScript((token) => {
        localStorage.setItem('karaoke_access_token', token)
      }, accessToken)
      console.log('Access token injected into localStorage')
    }

    const reviewUrl = buildReviewUrl(jobId, reviewToken)
    console.log(`Review URL : ${reviewUrl}`)

    // ── Step 1: Open the lyrics review page ──────────────────────────────────
    console.log('\n1. Opening lyrics review page…')
    await page.goto(reviewUrl, { waitUntil: 'networkidle' })
    await expect(page.locator('body')).not.toBeEmpty({ timeout: 30_000 })

    // Log visible buttons for debugging.
    const initialButtons = await page.getByRole('button').allTextContents()
    console.log(`   Visible buttons: ${initialButtons.slice(0, 10).join(' | ')}`)

    // ── Step 2: Assert duet mode is OFF initially ─────────────────────────────
    console.log('\n2. Verifying duet mode is off…')
    // Singer chips should not be present when duet mode is disabled.
    await expect(
      page.getByRole('button', { name: /Singer for this segment/i })
    ).toHaveCount(0, { timeout: 10_000 })
    console.log('   No singer chips — duet mode is off ✓')

    // ── Step 3: Enable duet mode ──────────────────────────────────────────────
    console.log('\n3. Enabling duet mode…')
    const duetToggle = page.getByRole('button', { name: /Mark as duet/i })
    await expect(duetToggle).toBeVisible({ timeout: 15_000 })
    await duetToggle.click()
    console.log('   Clicked "Mark as duet" button')

    // The button text should now change to "Duet: ON"
    await expect(page.getByRole('button', { name: /Duet: ON/i })).toBeVisible({
      timeout: 10_000,
    })
    console.log('   Button now shows "Duet: ON" ✓')

    // ── Step 4: Verify singer chips appeared ──────────────────────────────────
    console.log('\n4. Verifying singer chips appear…')
    const chips = page.getByRole('button', { name: /Singer for this segment/i })
    await expect(chips.first()).toBeVisible({ timeout: 10_000 })
    const chipCount = await chips.count()
    console.log(`   Found ${chipCount} singer chip(s) ✓`)

    // ── Step 5: Cycle first chip from Singer 1 → Singer 2 ────────────────────
    console.log('\n5. Cycling first chip to Singer 2…')
    const firstChip = chips.first()

    // Default state should display "1" (Singer 1).
    await expect(firstChip).toContainText('1', { timeout: 5_000 })
    console.log('   First chip shows "1" (Singer 1) ✓')

    await firstChip.click()

    // After one click it should cycle to "2" (Singer 2).
    await expect(firstChip).toContainText('2', { timeout: 5_000 })
    console.log('   First chip now shows "2" (Singer 2) ✓')

    // ── Step 6: Click "Preview Video" and capture the corrections POST ─────────
    // The lyrics review saves corrections to /api/jobs/{jobId}/corrections when
    // "Preview Video" is clicked (handleFinishReview → handleSubmitToServer).
    // We intercept this to verify the singer assignment is included.
    console.log('\n6. Clicking "Preview Video" and intercepting corrections save…')

    const correctionsPromise = page.waitForResponse(
      (r) =>
        r.url().includes('/corrections') &&
        r.request().method() === 'POST',
      { timeout: 30_000 }
    )

    const previewVideoBtn = page.getByRole('button', { name: /Preview Video/i })
    await expect(previewVideoBtn).toBeVisible({ timeout: 10_000 })
    await previewVideoBtn.click()
    console.log('   Clicked "Preview Video"')

    const correctionsResponse = await correctionsPromise
    console.log(`   Corrections POST status: ${correctionsResponse.status()}`)

    const correctionsBody = correctionsResponse.request().postDataJSON() as {
      corrections?: {
        is_duet?: boolean
        corrected_segments?: Array<{ singer?: number }>
      }
    }

    // Verify is_duet is saved in the corrections payload.
    // The payload structure is: { corrections: { ...data, is_duet, corrected_segments, ... } }
    // Note: is_duet lives on the outer CorrectionData, not nested in corrections.corrections
    const correctionData = correctionsBody.corrections ?? correctionsBody as any
    console.log(`   is_duet in payload: ${correctionData?.is_duet}`)
    expect(correctionData?.is_duet).toBe(true)

    // Verify the first segment has singer=2.
    const segments = correctionData?.corrected_segments ?? correctionData?.lines
    const firstSegSinger = segments?.[0]?.singer
    console.log(`   First segment singer: ${firstSegSinger}`)
    expect(firstSegSinger).toBe(2)

    console.log('\n─────────────────────────────────────────')
    console.log('DUET E2E COMPLETE ✓')
    console.log('  is_duet=true confirmed in corrections payload')
    console.log(`  corrected_segments[0].singer=${firstSegSinger} confirmed`)
    console.log('─────────────────────────────────────────')
  })

  test('verifies /complete endpoint receives is_duet=true after full duet flow', async ({
    page,
  }) => {
    /**
     * Full round-trip test that follows the flow all the way through the
     * instrumental review to the final POST /api/review/{jobId}/complete.
     *
     * This test is more invasive — it actually submits the review. Only run it
     * against a job that is expendable (e.g., a dedicated test job).
     *
     * This test is skipped unless E2E_RUN_FULL_DUET_FLOW=true is set.
     */
    test.skip(
      process.env.E2E_RUN_FULL_DUET_FLOW !== 'true',
      'Set E2E_RUN_FULL_DUET_FLOW=true to run the full duet round-trip (submits the review)'
    )

    test.setTimeout(300_000) // 5 min — includes preview video generation

    const jobId = getJobId()
    const reviewToken = getReviewToken()
    const accessToken = getAccessToken()

    if (!reviewToken && !accessToken) {
      throw new Error('Either E2E_REVIEW_TOKEN or E2E_TEST_TOKEN must be set')
    }

    if (accessToken && !reviewToken) {
      await page.addInitScript((token) => {
        localStorage.setItem('karaoke_access_token', token)
      }, accessToken)
    }

    // ── Lyrics review: enable duet + assign singer ────────────────────────────
    await page.goto(buildReviewUrl(jobId, reviewToken), { waitUntil: 'networkidle' })
    await expect(page.locator('body')).not.toBeEmpty({ timeout: 30_000 })

    // Toggle duet mode.
    const duetToggle = page.getByRole('button', { name: /Mark as duet/i })
    await expect(duetToggle).toBeVisible({ timeout: 15_000 })
    await duetToggle.click()
    await expect(page.getByRole('button', { name: /Duet: ON/i })).toBeVisible({ timeout: 10_000 })

    // Cycle first chip to Singer 2.
    const firstChip = page.getByRole('button', { name: /Singer for this segment/i }).first()
    await expect(firstChip).toBeVisible({ timeout: 10_000 })
    await expect(firstChip).toContainText('1', { timeout: 5_000 })
    await firstChip.click()
    await expect(firstChip).toContainText('2', { timeout: 5_000 })

    // Click "Preview Video" to trigger corrections save + open preview modal.
    await page.getByRole('button', { name: /Preview Video/i }).click()

    // Wait for the preview modal to open.
    const previewModal = page.getByRole('dialog')
    await expect(previewModal).toBeVisible({ timeout: 30_000 })

    // Wait for preview video generation to finish (may take several minutes).
    const loadingText = page.getByText(/generating preview video/i)
    if (await loadingText.isVisible({ timeout: 10_000 }).catch(() => false)) {
      console.log('Waiting for preview video generation…')
      await expect(loadingText).not.toBeVisible({ timeout: 600_000 })
    }

    // Click "Proceed to Instrumental Review".
    const proceedBtn = page.getByRole('button', { name: /Proceed to Instrumental/i })
    await expect(proceedBtn).toBeVisible({ timeout: 30_000 })
    await proceedBtn.click()

    // ── Instrumental review: intercept /complete POST ─────────────────────────
    // After proceeding, the page navigates (hash) to the instrumental review.
    // Wait for it to load and then submit.
    await page.waitForTimeout(3_000) // brief pause for hash navigation

    // Find and click the instrumental submit button.
    const submitBtn = page
      .getByRole('button', { name: /Looks Good|Submit|Confirm|Use This/i })
      .first()
    await expect(submitBtn).toBeVisible({ timeout: 30_000 })

    // Intercept the /complete POST before clicking submit.
    const completePromise = page.waitForResponse(
      (r) => r.url().includes('/complete') && r.request().method() === 'POST',
      { timeout: 60_000 }
    )

    await submitBtn.click()

    const completeResponse = await completePromise
    expect(completeResponse.status()).toBe(200)

    const body = completeResponse.request().postDataJSON() as {
      is_duet?: boolean
      corrected_segments?: Array<{ singer?: number }>
    }

    // Verify the final payload contains is_duet=true.
    expect(body.is_duet).toBe(true)

    // Verify the first segment's singer was persisted.
    const firstSegSinger = body.corrected_segments?.[0]?.singer
    expect(firstSegSinger).toBe(2)
  })
})
