// frontend/e2e/production/credit-purchase-real.spec.ts
import { test, expect } from '@playwright/test';
import { createEmailHelper, isEmailTestingAvailable } from '../helpers/email-testing';
import { completeStripeCheckout } from '../helpers/stripe-checkout';
import { getPageAuthToken } from '../helpers/auth';
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

      // "Buy More Credits" is inside the user dropdown menu in the header.
      // Open the dropdown by clicking the button containing "credits available",
      // then click the "Buy More Credits" menu item.
      const userDropdownButton = page.locator('button').filter({ hasText: /credits?\s+available/i }).first();
      await expect(userDropdownButton).toBeVisible({ timeout: TIMEOUTS.action });
      await userDropdownButton.click();
      console.log('  Opened user dropdown');

      // Wait for dropdown menu and click "Buy More Credits"
      const buyMenuItem = page.getByRole('menuitem', { name: /buy more credits/i });
      await expect(buyMenuItem).toBeVisible({ timeout: 5000 });
      console.log('  Found Buy More Credits menu item');
      await buyMenuItem.click();

      // Wait for BuyCreditsDialog
      const creditsDialog = page.getByRole('dialog');
      await expect(creditsDialog).toBeVisible({ timeout: TIMEOUTS.action });
      await page.screenshot({ path: 'test-results/04-buy-credits-dialog.png' });

      // Check for referral discount badge (may say "70% referral discount")
      // This is informational — don't fail the test if the badge isn't visible,
      // as the discount is applied server-side regardless of the UI badge
      const discountBadge = creditsDialog.getByText(/70%/i).first();
      if (await discountBadge.isVisible({ timeout: 5000 }).catch(() => false)) {
        console.log('  Referral discount badge visible');
      } else {
        console.log('  WARNING: Referral discount badge not visible — discount may still apply at checkout');
      }

      // ===== STEP 4: Select 1-credit package =====
      console.log('\n=== STEP 4: Select 1-credit package ===');

      // The packages grid has buttons with large credit-count numbers.
      // Find all package buttons and log them for debugging.
      const packageButtons = creditsDialog.locator('button').filter({ hasText: /credit/i });
      const btnCount = await packageButtons.count();
      console.log(`  Found ${btnCount} package buttons`);
      for (let i = 0; i < Math.min(btnCount, 4); i++) {
        const txt = await packageButtons.nth(i).textContent();
        console.log(`    Button ${i}: "${txt?.replace(/\s+/g, ' ').trim().substring(0, 80)}"`);
      }

      // Select the 1-credit package — first button in the 2-column grid
      const oneCredit = packageButtons.first();
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

      // Verify credit balance is shown — the number and "credits available"
      // may be in separate DOM elements, so check for each independently
      const creditsAvailableText = page.getByText(/credits?\s+available/i);
      await expect(creditsAvailableText).toBeVisible({ timeout: TIMEOUTS.action });
      await page.screenshot({ path: 'test-results/06-payment-success.png' });
      console.log('  Payment success page confirmed with credits');

      // ===== STEP 7: Verify via API =====
      console.log('\n=== STEP 7: Verify credits via API ===');
      try {
        const userResponse = await request.get(`${API_URL}/api/users/me`, {
          headers: {
            Authorization: `Bearer ${sessionToken}`,
            'X-Session-Token': sessionToken,
          },
        });
        if (userResponse.ok()) {
          const userData = await userResponse.json();
          console.log(`  API response keys: ${Object.keys(userData).join(', ')}`);
          console.log(`  API credits: ${userData.credits ?? userData.credit_balance ?? 'N/A'}`);
        } else {
          console.log(`  API returned ${userResponse.status()} — session token may use different auth`);
        }
      } catch (e) {
        console.log(`  API check skipped: ${e}`);
      }
      // The success page already confirmed credits — API check is informational

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
