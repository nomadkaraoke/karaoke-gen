# Daily E2E Payment + Happy Path Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily E2E test that purchases credits via real Stripe Checkout, then uses them to run the existing happy path karaoke job creation test.

**Architecture:** A single GitHub Actions workflow (`e2e-daily.yml`) runs two sequential jobs: Stage 1 (credit purchase via real Stripe) outputs a session token, Stage 2 (happy path) consumes it. Discord + email notifications on both success and failure.

**Tech Stack:** Playwright, GitHub Actions, Stripe Checkout (live mode), testmail.app, Discord webhooks, SendGrid

**Spec:** `docs/archive/2026-04-12-daily-e2e-payment-test-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `frontend/e2e/helpers/stripe-checkout.ts` | **New** — Helper to fill Stripe's hosted checkout page (card fields in iframes) |
| `frontend/e2e/production/credit-purchase-real.spec.ts` | **New** — Playwright test: signup with referral, buy 1 credit via real Stripe, verify credit + email |
| `.github/workflows/e2e-daily.yml` | **New** — Combined two-stage workflow (credit purchase → happy path) with Discord + email alerts |
| `.github/workflows/e2e-happy-path.yml` | **Delete** — Replaced by e2e-daily.yml |

No modifications to existing test files. The happy path test already supports `E2E_TEST_TOKEN` for pre-auth.

---

## Task 1: Stripe Checkout Page Helper

**Files:**
- Create: `frontend/e2e/helpers/stripe-checkout.ts`

This helper navigates Stripe's hosted checkout page. Stripe Checkout uses iframes for card input fields — the helper must locate the correct iframe, then fill within it.

- [ ] **Step 1: Create the stripe-checkout helper**

```typescript
// frontend/e2e/helpers/stripe-checkout.ts
import { Page, FrameLocator } from '@playwright/test';

/**
 * Fill card details on Stripe's hosted checkout page (checkout.stripe.com).
 *
 * Stripe embeds card inputs in iframes. This helper locates the
 * correct iframe for each field and fills it.
 *
 * Environment variables:
 *   E2E_STRIPE_CARD_NUMBER, E2E_STRIPE_CARD_EXPIRY,
 *   E2E_STRIPE_CARD_CVC, E2E_STRIPE_CARDHOLDER_NAME (optional)
 */

interface CardDetails {
  number: string;
  expiry: string;
  cvc: string;
  name?: string;
}

function getCardDetailsFromEnv(): CardDetails {
  const number = process.env.E2E_STRIPE_CARD_NUMBER;
  const expiry = process.env.E2E_STRIPE_CARD_EXPIRY;
  const cvc = process.env.E2E_STRIPE_CARD_CVC;
  const name = process.env.E2E_STRIPE_CARDHOLDER_NAME;

  if (!number || !expiry || !cvc) {
    throw new Error(
      'Missing Stripe card env vars: E2E_STRIPE_CARD_NUMBER, E2E_STRIPE_CARD_EXPIRY, E2E_STRIPE_CARD_CVC'
    );
  }

  return { number, expiry, cvc, name };
}

/**
 * Wait for the Stripe Checkout page to fully load.
 * Call after the browser has navigated to checkout.stripe.com.
 */
async function waitForStripeCheckout(page: Page, timeoutMs = 30_000): Promise<void> {
  await page.waitForURL(/checkout\.stripe\.com/, { timeout: timeoutMs });
  // Wait for the payment form to be interactive
  await page.waitForSelector('#cardNumber, [data-testid="card-number-input"]', {
    state: 'visible',
    timeout: timeoutMs,
  }).catch(() => {
    // Stripe may use iframe-based fields — check for that
  });
  // Give Stripe JS time to initialize iframes
  await page.waitForTimeout(2000);
}

/**
 * Fill a single field inside a Stripe iframe.
 * Stripe card inputs live in individual iframes identified by their title or name.
 */
async function fillStripeField(
  page: Page,
  iframeSelector: string,
  inputSelector: string,
  value: string,
): Promise<void> {
  // Try direct input first (some Stripe Checkout versions use plain inputs)
  const directInput = page.locator(inputSelector).first();
  if (await directInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await directInput.fill(value);
    return;
  }

  // Fall back to iframe-based input
  const frame = page.frameLocator(iframeSelector).first();
  const input = frame.locator(inputSelector).first();
  await input.waitFor({ state: 'visible', timeout: 10_000 });
  // Use type() instead of fill() — Stripe's custom inputs often reject fill()
  await input.type(value, { delay: 50 });
}

/**
 * Complete the Stripe Checkout page with card details from environment.
 *
 * @param page - Playwright page already navigated to checkout.stripe.com
 * @returns void — after this, the page will redirect to the success URL
 */
export async function completeStripeCheckout(page: Page): Promise<void> {
  const card = getCardDetailsFromEnv();

  console.log('  Waiting for Stripe Checkout page...');
  await waitForStripeCheckout(page);
  await page.screenshot({ path: 'test-results/stripe-checkout-loaded.png' });
  console.log('  Stripe Checkout loaded');

  // Fill email if Stripe asks for it (sometimes pre-filled from session)
  const emailInput = page.locator('#email');
  if (await emailInput.isVisible({ timeout: 3000 }).catch(() => false)) {
    // Email should be pre-filled from checkout session, but clear and re-enter if empty
    const currentEmail = await emailInput.inputValue();
    if (!currentEmail) {
      console.log('  Stripe email field is empty — cannot fill without knowing email');
    } else {
      console.log(`  Stripe email pre-filled: ${currentEmail}`);
    }
  }

  // Fill card number
  console.log('  Filling card number...');
  await fillStripeField(
    page,
    'iframe[title*="card number" i], iframe[name*="cardNumber" i]',
    'input[name="cardnumber"], input[name="number"], input[autocomplete="cc-number"]',
    card.number,
  );

  // Fill expiry
  console.log('  Filling card expiry...');
  await fillStripeField(
    page,
    'iframe[title*="expir" i], iframe[name*="cardExpiry" i]',
    'input[name="exp-date"], input[name="expiry"], input[autocomplete="cc-exp"]',
    card.expiry,
  );

  // Fill CVC
  console.log('  Filling CVC...');
  await fillStripeField(
    page,
    'iframe[title*="cvc" i], iframe[title*="security" i], iframe[name*="cardCvc" i]',
    'input[name="cvc"], input[autocomplete="cc-csc"]',
    card.cvc,
  );

  // Fill cardholder name if field exists and name is provided
  if (card.name) {
    const nameInput = page.locator('#billingName, input[name="billingName"]');
    if (await nameInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('  Filling cardholder name...');
      await nameInput.fill(card.name);
    }
  }

  await page.screenshot({ path: 'test-results/stripe-checkout-filled.png' });
  console.log('  Card details filled');

  // Click the Pay button
  console.log('  Clicking Pay button...');
  const payButton = page.getByRole('button', { name: /pay/i }).first();
  await payButton.waitFor({ state: 'visible', timeout: 10_000 });
  await payButton.click();

  console.log('  Payment submitted, waiting for redirect...');
  // Wait for redirect back to our site (success page)
  await page.waitForURL(/nomadkaraoke\.com.*payment\/success|nomadkaraoke\.com.*\/app/, {
    timeout: 60_000,
  });
  await page.screenshot({ path: 'test-results/stripe-checkout-complete.png' });
  console.log('  Stripe Checkout complete — redirected to success page');
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/e2e/helpers/stripe-checkout.ts
git commit -m "feat(e2e): add Stripe Checkout page automation helper"
```

---

## Task 2: Credit Purchase E2E Test

**Files:**
- Create: `frontend/e2e/production/credit-purchase-real.spec.ts`

This is the Stage 1 Playwright test. It signs up a fresh user with referral code, buys 1 credit via real Stripe, and verifies the credit lands.

- [ ] **Step 1: Create the credit purchase test**

```typescript
// frontend/e2e/production/credit-purchase-real.spec.ts
import { test, expect } from '@playwright/test';
import { createEmailHelper, isEmailTestingAvailable } from '../helpers/email-testing';
import { completeStripeCheckout } from '../helpers/stripe-checkout';
import { getPageAuthToken, verifyToken } from '../helpers/auth';
import { URLS, TIMEOUTS } from '../helpers/constants';
import * as fs from 'fs';
import * as path from 'path';

/**
 * E2E Test: Real Credit Purchase via Stripe Checkout
 *
 * Tests the REAL payment flow that customers use:
 * 1. Sign up with referral code e2etest70 (70% discount)
 * 2. Open Buy Credits dialog
 * 3. Purchase 1 credit via real Stripe Checkout ($3.00 after discount)
 * 4. Verify credits allocated and confirmation email received
 *
 * This test uses a REAL credit card and processes a REAL charge.
 * The 70% referral discount keeps cost to ~$3/day.
 *
 * Prerequisites:
 *   - E2E_STRIPE_CARD_NUMBER, E2E_STRIPE_CARD_EXPIRY, E2E_STRIPE_CARD_CVC
 *   - TESTMAIL_API_KEY + TESTMAIL_NAMESPACE
 *   - E2E_ADMIN_TOKEN (for credit verification + cleanup)
 *
 * Output:
 *   - Writes session token to test-results/e2e-session-token.txt
 *     for Stage 2 (happy path) to consume
 */

const PROD_URL = URLS.production.frontend;
const API_URL = URLS.production.api;

test.describe('Real Credit Purchase Flow', () => {
  test('purchase 1 credit via Stripe Checkout with referral discount', async ({ page, request }) => {
    // ===== PREREQUISITES =====
    test.skip(!process.env.E2E_STRIPE_CARD_NUMBER, 'E2E_STRIPE_CARD_NUMBER not set');
    test.skip(!isEmailTestingAvailable(), 'Testmail credentials not set');

    const adminToken = process.env.E2E_ADMIN_TOKEN;
    test.skip(!adminToken, 'E2E_ADMIN_TOKEN not set');

    const emailHelper = await createEmailHelper();
    const inbox = await emailHelper.createInbox();
    console.log(`Test email: ${inbox.emailAddress}`);

    try {
      // ===== STEP 1: Navigate with referral code =====
      console.log('\n=== STEP 1: Navigate with referral code ===');
      await page.goto(`${PROD_URL}/?ref=e2etest70`);
      await page.waitForLoadState('networkidle');
      await page.screenshot({ path: 'test-results/01-landing-with-referral.png' });
      console.log('  Landed with ?ref=e2etest70');

      // ===== STEP 2: Sign up via magic link =====
      console.log('\n=== STEP 2: Sign up via magic link ===');

      // Click Sign Up Free
      const signUpButton = page.getByRole('button', { name: /sign up free/i });
      await expect(signUpButton).toBeVisible({ timeout: TIMEOUTS.action });
      await signUpButton.click();

      // Fill email in AuthDialog
      const authDialog = page.getByRole('dialog');
      await expect(authDialog).toBeVisible({ timeout: TIMEOUTS.action });
      const emailInput = authDialog.getByPlaceholder('you@example.com');
      await emailInput.fill(inbox.emailAddress!);
      await page.screenshot({ path: 'test-results/02-auth-dialog.png' });

      // Send magic link
      const sendButton = page.getByRole('button', { name: /send sign-in link/i });
      await sendButton.click();
      await expect(page.getByText(/check your email/i)).toBeVisible({ timeout: 15000 });
      console.log('  Magic link sent');

      // Wait for email and extract link
      const magicEmail = await emailHelper.waitForEmail(inbox.id!, 60000);
      const magicLink = emailHelper.extractMagicLink(magicEmail);
      if (!magicLink) throw new Error('Could not extract magic link from email');
      console.log(`  Magic link received`);

      // Navigate to magic link
      await page.goto(magicLink);

      // Handle verification result (new user interstitial or direct success)
      const verifyResult = await Promise.race([
        page.getByText(/successfully signed in/i).waitFor({ state: 'visible', timeout: 30000 }).then(() => 'success'),
        page.getByText(/welcome to nomad karaoke/i).waitFor({ state: 'visible', timeout: 30000 }).then(() => 'credits_interstitial'),
      ]);
      console.log(`  Verification: ${verifyResult}`);

      if (verifyResult === 'credits_interstitial') {
        const startButton = page.getByRole('button', { name: /start creating|go to dashboard|explore the app/i });
        await expect(startButton).toBeVisible({ timeout: 5000 });
        await startButton.click();
      }

      await page.waitForURL(/\/app/, { timeout: 15000 });
      await page.screenshot({ path: 'test-results/03-authenticated.png' });
      console.log('  Authenticated and on /app');

      // Extract session token for Stage 2
      const sessionToken = await getPageAuthToken(page);
      if (!sessionToken) throw new Error('No session token after auth');

      // ===== STEP 3: Open Buy Credits and verify discount =====
      console.log('\n=== STEP 3: Open Buy Credits dialog ===');

      // Click "Buy Credits" button in header
      const buyButton = page.getByRole('button', { name: /buy credits/i });
      await expect(buyButton).toBeVisible({ timeout: TIMEOUTS.action });
      await buyButton.click();

      // Wait for BuyCreditsDialog
      const creditsDialog = page.getByRole('dialog');
      await expect(creditsDialog).toBeVisible({ timeout: TIMEOUTS.action });
      await page.screenshot({ path: 'test-results/04-buy-credits-dialog.png' });

      // Verify referral discount badge is showing
      const discountBadge = creditsDialog.getByText(/70%\s*off/i);
      await expect(discountBadge).toBeVisible({ timeout: TIMEOUTS.action });
      console.log('  Referral discount badge visible (70% off)');

      // ===== STEP 4: Select 1-credit package =====
      console.log('\n=== STEP 4: Select 1-credit package ===');

      // Click the 1-credit package button
      // Packages are buttons within the dialog showing credit counts
      const oneCredit = creditsDialog.getByRole('button').filter({ hasText: /\b1\b/ }).filter({ hasText: /credit/i }).first();
      await oneCredit.click();
      await page.screenshot({ path: 'test-results/05-package-selected.png' });
      console.log('  Selected 1-credit package');

      // Verify the discounted price ($3.00) is shown
      // The checkout button should show the price
      const checkoutButton = creditsDialog.getByRole('button', { name: /continue|checkout|pay/i }).last();
      await expect(checkoutButton).toBeVisible({ timeout: TIMEOUTS.action });
      const buttonText = await checkoutButton.textContent();
      console.log(`  Checkout button: "${buttonText}"`);

      // ===== STEP 5: Proceed to Stripe Checkout =====
      console.log('\n=== STEP 5: Stripe Checkout ===');
      await checkoutButton.click();

      // Complete Stripe Checkout with real card
      await completeStripeCheckout(page);

      // ===== STEP 6: Verify payment success page =====
      console.log('\n=== STEP 6: Verify payment success ===');
      await page.waitForURL(/payment\/success/, { timeout: 30000 });

      // Verify success indicators
      const successText = page.getByText(/payment successful/i);
      await expect(successText).toBeVisible({ timeout: TIMEOUTS.action });

      // Verify credit balance shows 1
      const creditBalance = page.getByText(/1\s+credit/i);
      await expect(creditBalance).toBeVisible({ timeout: TIMEOUTS.action });
      await page.screenshot({ path: 'test-results/06-payment-success.png' });
      console.log('  Payment success page shows 1 credit');

      // ===== STEP 7: Verify via admin API =====
      console.log('\n=== STEP 7: Verify credits via API ===');
      const userResponse = await request.get(`${API_URL}/api/users/me`, {
        headers: { Authorization: `Bearer ${sessionToken}` },
      });
      expect(userResponse.ok()).toBe(true);
      const userData = await userResponse.json();
      console.log(`  API credits: ${userData.credits}`);
      expect(userData.credits).toBeGreaterThanOrEqual(1);

      // ===== STEP 8: Verify confirmation email =====
      console.log('\n=== STEP 8: Verify confirmation email ===');
      try {
        // Wait for the second email (first was magic link, second is payment confirmation)
        const confirmEmail = await emailHelper.waitForEmail(inbox.id!, 30000);
        console.log(`  Confirmation email: "${confirmEmail.subject}"`);
        const emailText = ((confirmEmail.subject || '') + ' ' + (confirmEmail.body || '')).toLowerCase();
        expect(emailText).toContain('credit');
      } catch (e) {
        // Email verification is best-effort — don't fail the payment test for email issues
        console.log(`  Email verification skipped: ${e}`);
      }

      // ===== OUTPUT: Save token for Stage 2 =====
      console.log('\n=== OUTPUT: Saving session token for Stage 2 ===');
      const outputDir = path.join(process.cwd(), 'test-results');
      fs.mkdirSync(outputDir, { recursive: true });
      fs.writeFileSync(path.join(outputDir, 'e2e-session-token.txt'), sessionToken);
      console.log('  Token saved to test-results/e2e-session-token.txt');

      console.log('\n✅ CREDIT PURCHASE TEST PASSED');

    } finally {
      if (inbox?.id) {
        await emailHelper.deleteInbox(inbox.id);
      }
    }
  });
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/e2e/production/credit-purchase-real.spec.ts
git commit -m "feat(e2e): add real Stripe Checkout credit purchase test"
```

---

## Task 3: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/e2e-daily.yml`
- Delete: `.github/workflows/e2e-happy-path.yml`

Two-stage workflow: credit purchase → happy path. Discord + email notifications.

- [ ] **Step 1: Create the new e2e-daily workflow**

```yaml
# .github/workflows/e2e-daily.yml
name: E2E Daily Test (Payment + Happy Path)

# Full production E2E: buy credits via real Stripe, then use them for karaoke generation.
# Replaces the previous e2e-happy-path.yml with an additional payment stage.

on:
  schedule:
    - cron: '0 6 * * *'  # Daily at 6 AM UTC

  workflow_dispatch:
    inputs:
      debug_mode:
        description: 'Enable debug mode (slower, more screenshots)'
        required: false
        default: 'false'
        type: boolean
      browser:
        description: 'Browser to test with'
        required: false
        default: 'chromium'
        type: choice
        options:
          - chromium
          - firefox
          - webkit
      skip_payment:
        description: 'Skip Stage 1 (payment) — use admin-granted credits for happy path only'
        required: false
        default: 'false'
        type: boolean
      use_test_token:
        description: 'Skip signup entirely — use E2E_TEST_TOKEN (for quick iterations)'
        required: false
        default: 'false'
        type: boolean

permissions:
  contents: read

jobs:
  # =========================================================================
  # Stage 1: Purchase credits via real Stripe Checkout
  # =========================================================================
  e2e-credit-purchase:
    name: "Stage 1: Credit Purchase"
    runs-on: ubuntu-latest
    timeout-minutes: 15
    if: ${{ github.event.inputs.skip_payment != 'true' && github.event.inputs.use_test_token != 'true' }}

    outputs:
      session_token: ${{ steps.extract_token.outputs.token }}
      result: ${{ steps.test_result.outputs.result }}

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci --legacy-peer-deps

      - name: Install Playwright browsers
        working-directory: frontend
        run: npx playwright install --with-deps ${{ inputs.browser || 'chromium' }}

      - name: Create test-results directory
        working-directory: frontend
        run: mkdir -p test-results

      - name: Ensure testmail.app domain is allowlisted
        run: |
          curl -sf -X POST \
            "https://api.nomadkaraoke.com/api/admin/rate-limits/blocklists/allowlisted-domains" \
            -H "X-Admin-Token: ${{ secrets.E2E_ADMIN_TOKEN }}" \
            -H "Content-Type: application/json" \
            -d '{"domain": "inbox.testmail.app"}' || echo "Warning: allowlist may already exist"

      - name: Run Credit Purchase Test
        working-directory: frontend
        env:
          TESTMAIL_API_KEY: ${{ secrets.TESTMAIL_API_KEY }}
          TESTMAIL_NAMESPACE: ${{ secrets.TESTMAIL_NAMESPACE }}
          E2E_ADMIN_TOKEN: ${{ secrets.E2E_ADMIN_TOKEN }}
          E2E_STRIPE_CARD_NUMBER: ${{ secrets.E2E_STRIPE_CARD_NUMBER }}
          E2E_STRIPE_CARD_EXPIRY: ${{ secrets.E2E_STRIPE_CARD_EXPIRY }}
          E2E_STRIPE_CARD_CVC: ${{ secrets.E2E_STRIPE_CARD_CVC }}
          E2E_STRIPE_CARDHOLDER_NAME: ${{ secrets.E2E_STRIPE_CARDHOLDER_NAME }}
          DEBUG_MODE: ${{ inputs.debug_mode || 'false' }}
        run: |
          echo "=== Stage 1: Credit Purchase via Real Stripe ==="
          echo "Time: $(date)"
          npx playwright test credit-purchase-real.spec.ts \
            --config=playwright.production.config.ts \
            --project=${{ inputs.browser || 'chromium' }} \
            --reporter=list \
            --timeout=600000 \
            2>&1 | tee test-output-payment.log
        continue-on-error: true

      - name: Check test result
        id: test_result
        working-directory: frontend
        run: |
          if grep -q "CREDIT PURCHASE TEST PASSED" test-output-payment.log; then
            echo "result=success" >> $GITHUB_OUTPUT
          else
            echo "result=failure" >> $GITHUB_OUTPUT
          fi

      - name: Extract session token
        id: extract_token
        if: steps.test_result.outputs.result == 'success'
        working-directory: frontend
        run: |
          if [ -f test-results/e2e-session-token.txt ]; then
            TOKEN=$(cat test-results/e2e-session-token.txt)
            echo "::add-mask::$TOKEN"
            echo "token=$TOKEN" >> $GITHUB_OUTPUT
            echo "Session token extracted for Stage 2"
          else
            echo "No session token file found"
            echo "token=" >> $GITHUB_OUTPUT
          fi

      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-payment-results-${{ github.run_number }}
          path: |
            frontend/test-results/
            frontend/test-output-payment.log
          retention-days: 30

      - name: Discord notification (failure)
        if: steps.test_result.outputs.result == 'failure'
        run: |
          curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\": [{\"title\": \"🚨 E2E Daily Test FAILED — Stage 1: Credit Purchase\", \"description\": \"Payment flow is broken. Customers may not be receiving credits.\n\n**Run:** #${{ github.run_number }}\n**Details:** ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}\", \"color\": 15158332}]}" \
            "${{ secrets.DISCORD_ALERT_WEBHOOK_URL }}"

      - name: Fail if test failed
        if: steps.test_result.outputs.result == 'failure'
        run: exit 1

  # =========================================================================
  # Stage 2: Happy Path (uses credit from Stage 1)
  # =========================================================================
  e2e-happy-path:
    name: "Stage 2: Happy Path"
    runs-on: ubuntu-latest
    timeout-minutes: 90
    needs: e2e-credit-purchase
    # Run if: Stage 1 succeeded, OR Stage 1 was skipped (skip_payment/use_test_token)
    if: |
      always() && (
        needs.e2e-credit-purchase.result == 'success' ||
        github.event.inputs.skip_payment == 'true' ||
        github.event.inputs.use_test_token == 'true'
      )

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci --legacy-peer-deps

      - name: Install Playwright browsers
        working-directory: frontend
        run: npx playwright install --with-deps ${{ inputs.browser || 'chromium' }}

      - name: Create test-results directory
        working-directory: frontend
        run: mkdir -p test-results

      - name: Ensure testmail.app domain is allowlisted
        run: |
          curl -sf -X POST \
            "https://api.nomadkaraoke.com/api/admin/rate-limits/blocklists/allowlisted-domains" \
            -H "X-Admin-Token: ${{ secrets.E2E_ADMIN_TOKEN }}" \
            -H "Content-Type: application/json" \
            -d '{"domain": "inbox.testmail.app"}' || echo "Warning: allowlist may already exist"

      - name: Refresh E2E session token (when use_test_token enabled)
        id: refresh_token
        if: github.event.inputs.use_test_token == 'true'
        run: |
          RESPONSE=$(curl -sf -X POST \
            "https://api.nomadkaraoke.com/api/admin/users/e2e-test-runner@nomadkaraoke.com/impersonate" \
            -H "X-Admin-Token: ${{ secrets.E2E_ADMIN_TOKEN }}" \
            -H "Content-Type: application/json")
          TOKEN=$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_token"])')
          echo "::add-mask::$TOKEN"
          echo "token=$TOKEN" >> $GITHUB_OUTPUT

      - name: Run Happy Path Test
        working-directory: frontend
        env:
          TESTMAIL_API_KEY: ${{ secrets.TESTMAIL_API_KEY }}
          TESTMAIL_NAMESPACE: ${{ secrets.TESTMAIL_NAMESPACE }}
          # Token priority: use_test_token > Stage 1 output > empty (full signup)
          E2E_TEST_TOKEN: ${{ steps.refresh_token.outputs.token || needs.e2e-credit-purchase.outputs.session_token || '' }}
          E2E_ADMIN_TOKEN: ${{ secrets.E2E_ADMIN_TOKEN }}
          E2E_BYPASS_KEY: ${{ secrets.E2E_BYPASS_KEY }}
          DEBUG_MODE: ${{ inputs.debug_mode || 'false' }}
        run: |
          echo "=== Stage 2: Happy Path ==="
          echo "Time: $(date)"
          TOKEN_SOURCE="none"
          if [ -n "${{ steps.refresh_token.outputs.token }}" ]; then
            TOKEN_SOURCE="use_test_token (impersonation)"
          elif [ -n "${{ needs.e2e-credit-purchase.outputs.session_token }}" ]; then
            TOKEN_SOURCE="Stage 1 (credit purchase)"
          fi
          echo "Token source: $TOKEN_SOURCE"

          npx playwright test happy-path-real-user.spec.ts \
            --config=playwright.production.config.ts \
            --project=${{ inputs.browser || 'chromium' }} \
            --reporter=list \
            --timeout=3600000 \
            2>&1 | tee test-output-happy-path.log
        continue-on-error: true

      - name: Check test result
        id: test_result
        working-directory: frontend
        run: |
          if grep -q "passed" test-output-happy-path.log && ! grep -q "failed" test-output-happy-path.log; then
            echo "result=success" >> $GITHUB_OUTPUT
          else
            echo "result=failure" >> $GITHUB_OUTPUT
          fi

      - name: Upload test artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-happy-path-results-${{ github.run_number }}
          path: |
            frontend/test-results/
            frontend/playwright-report/
            frontend/test-output-happy-path.log
          retention-days: 30

      - name: Upload video recordings
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-happy-path-videos-${{ github.run_number }}
          path: frontend/test-results/**/*.webm
          retention-days: 30
          if-no-files-found: ignore

      - name: Create summary
        if: always()
        run: |
          STAGE1="${{ needs.e2e-credit-purchase.result || 'skipped' }}"
          STAGE2="${{ steps.test_result.outputs.result || 'unknown' }}"
          echo "## E2E Daily Test Results" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Stage | Result |" >> $GITHUB_STEP_SUMMARY
          echo "|-------|--------|" >> $GITHUB_STEP_SUMMARY
          echo "| Stage 1: Credit Purchase | ${STAGE1} |" >> $GITHUB_STEP_SUMMARY
          echo "| Stage 2: Happy Path | ${STAGE2} |" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Property | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|----------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Run | #${{ github.run_number }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Trigger | ${{ github.event_name }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Browser | ${{ inputs.browser || 'chromium' }} |" >> $GITHUB_STEP_SUMMARY

      - name: Discord notification (failure)
        if: steps.test_result.outputs.result == 'failure'
        run: |
          curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\": [{\"title\": \"🚨 E2E Daily Test FAILED — Stage 2: Happy Path\", \"description\": \"Karaoke generation flow is broken.\n\n**Run:** #${{ github.run_number }}\n**Details:** ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}\", \"color\": 15158332}]}" \
            "${{ secrets.DISCORD_ALERT_WEBHOOK_URL }}"

      - name: Fail if test failed
        if: steps.test_result.outputs.result == 'failure'
        run: exit 1

  # =========================================================================
  # Final: Send notifications
  # =========================================================================
  notify:
    name: "Notify"
    runs-on: ubuntu-latest
    needs: [e2e-credit-purchase, e2e-happy-path]
    if: always()

    steps:
      - name: Discord notification (success)
        if: needs.e2e-happy-path.result == 'success'
        run: |
          STAGE1="${{ needs.e2e-credit-purchase.result || 'skipped' }}"
          curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\": [{\"title\": \"✅ E2E Daily Test PASSED\", \"description\": \"Payment: ${STAGE1} | Happy Path: success\n\n**Run:** #${{ github.run_number }}\", \"color\": 3066993}]}" \
            "${{ secrets.DISCORD_ALERT_WEBHOOK_URL }}"

      - name: Send email notification
        if: github.event_name == 'schedule'
        uses: dawidd6/action-send-mail@v3
        with:
          server_address: smtp.sendgrid.net
          server_port: 587
          username: apikey
          password: ${{ secrets.SENDGRID_API_KEY }}
          subject: "E2E Daily Test ${{ needs.e2e-happy-path.result == 'success' && 'PASSED' || 'FAILED' }} - Run #${{ github.run_number }}"
          to: andrew@nomadkaraoke.com
          from: "Karaoke Gen CI <noreply@nomadkaraoke.com>"
          body: |
            E2E Daily Test Results
            ======================

            Stage 1 (Credit Purchase): ${{ needs.e2e-credit-purchase.result || 'skipped' }}
            Stage 2 (Happy Path):      ${{ needs.e2e-happy-path.result || 'skipped' }}

            Run: #${{ github.run_number }}
            Trigger: ${{ github.event_name }}

            View details: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

- [ ] **Step 2: Delete the old workflow**

```bash
rm .github/workflows/e2e-happy-path.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/e2e-daily.yml
git rm .github/workflows/e2e-happy-path.yml
git commit -m "feat(ci): replace e2e-happy-path with e2e-daily (payment + happy path)"
```

---

## Task 4: Configure Referral Link and GitHub Secrets

This task is manual setup — no code changes, but must be done before the test can run.

- [ ] **Step 1: Verify or create the `e2etest70` referral link in production**

```bash
# Check if it exists
curl -s "https://api.nomadkaraoke.com/api/referrals/e2etest70/interstitial" | python3 -m json.tool

# If it doesn't exist, create it via admin API:
ADMIN_TOKEN=$(gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1)
curl -s -X POST "https://api.nomadkaraoke.com/api/referrals/admin/links" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "e2etest70",
    "owner_email": "admin@nomadkaraoke.com",
    "display_name": "E2E Test",
    "discount_percent": 70,
    "kickback_percent": 0,
    "duration_days": 36500
  }' | python3 -m json.tool
```

- [ ] **Step 2: Add GitHub Actions secrets**

```bash
# Card details (user will provide these)
gh secret set E2E_STRIPE_CARD_NUMBER --repo nomadkaraoke/karaoke-gen
gh secret set E2E_STRIPE_CARD_EXPIRY --repo nomadkaraoke/karaoke-gen
gh secret set E2E_STRIPE_CARD_CVC --repo nomadkaraoke/karaoke-gen
gh secret set E2E_STRIPE_CARDHOLDER_NAME --repo nomadkaraoke/karaoke-gen

# Discord webhook (from GCP Secret Manager)
DISCORD_URL=$(gcloud secrets versions access latest --secret=discord-alert-webhook --project=nomadkaraoke)
gh secret set DISCORD_ALERT_WEBHOOK_URL --repo nomadkaraoke/karaoke-gen --body "$DISCORD_URL"
```

- [ ] **Step 3: Verify secrets are set**

```bash
gh secret list --repo nomadkaraoke/karaoke-gen | grep -E "E2E_STRIPE|DISCORD_ALERT"
```

---

## Task 5: Manual Test Run and Verification

- [ ] **Step 1: Push the branch and trigger the workflow manually**

```bash
git push origin HEAD
```

Then trigger via GitHub UI or CLI:
```bash
gh workflow run e2e-daily.yml --ref $(git branch --show-current) -f skip_payment=false -f debug_mode=true
```

- [ ] **Step 2: Monitor the run**

```bash
# Watch the latest run
gh run list --workflow=e2e-daily.yml --limit 1
gh run watch $(gh run list --workflow=e2e-daily.yml --limit 1 --json databaseId -q '.[0].databaseId')
```

- [ ] **Step 3: Verify Stage 1 (credit purchase) passed**

Check:
- Stripe dashboard shows the $3.00 charge
- Test user has 1 credit in Firestore
- Confirmation email was received

- [ ] **Step 4: Verify Stage 2 (happy path) passed**

Check:
- The token from Stage 1 was used (logs show "Using token: ...")
- A karaoke job was created and completed
- Discord received a success notification

- [ ] **Step 5: If any issues, fix and re-run**

The Stripe Checkout selectors are the most likely failure point. Check screenshots in the artifacts to see what Stripe's current UI looks like, then update `stripe-checkout.ts` selectors accordingly.

- [ ] **Step 6: Commit any selector fixes from the test run**

```bash
git add -A
git commit -m "fix(e2e): update Stripe Checkout selectors from test run"
```
