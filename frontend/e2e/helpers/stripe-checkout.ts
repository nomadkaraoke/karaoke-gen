// frontend/e2e/helpers/stripe-checkout.ts
import { Page } from '@playwright/test';

/**
 * Automate Stripe's hosted checkout page (checkout.stripe.com).
 *
 * Stripe Checkout (as of April 2026) shows:
 * - "Pay with Link" / "Pay with Klarna" buttons at top
 * - Payment method radio buttons: Card, Cash App Pay, Klarna, Bank
 * - Clicking "Card" reveals card input fields (number, expiry, CVC)
 * - Card fields are inside iframes within the page
 * - A "Pay" button at the bottom
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
 * Complete the Stripe Checkout page with card details from environment.
 *
 * @param page - Playwright page that will be redirected to checkout.stripe.com
 * @returns void — after this, the page will redirect to the success URL
 */
export async function completeStripeCheckout(page: Page): Promise<void> {
  const card = getCardDetailsFromEnv();

  // Step 1: Wait for Stripe Checkout page to fully load
  console.log('  Waiting for Stripe Checkout page...');
  await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 });
  // Wait for the page to be fully interactive — look for the "Pay" button
  // which is one of the last elements to render
  await page.getByRole('button', { name: /^Pay$/i }).waitFor({
    state: 'visible',
    timeout: 30_000,
  });
  await page.screenshot({ path: 'test-results/stripe-checkout-loaded.png' });
  console.log('  Stripe Checkout loaded');

  // Step 2: Select "Card" payment method
  // Stripe uses an accordion with radio buttons overlaid by buttons.
  // Use force:true to bypass the overlay interception.
  console.log('  Selecting Card payment method...');
  const cardRadio = page.getByRole('radio', { name: 'Card' });
  await cardRadio.check({ force: true, timeout: 10_000 });
  console.log('  Card payment method selected');

  // Wait for card input fields to appear after selecting Card
  // Stripe renders card fields inside iframes after the radio is clicked
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'test-results/stripe-card-selected.png' });

  // Step 3: Fill card number
  // After clicking "Card", Stripe shows card input fields.
  // These may be direct inputs (#cardNumber) or inside iframes.
  console.log('  Filling card number...');
  await fillCardField(page, 'cardNumber', card.number);

  // Step 4: Fill expiry
  console.log('  Filling card expiry...');
  await fillCardField(page, 'cardExpiry', card.expiry);

  // Step 5: Fill CVC
  console.log('  Filling CVC...');
  await fillCardField(page, 'cardCvc', card.cvc);

  // Step 6: Fill cardholder name if field exists
  if (card.name) {
    const nameInput = page.locator('#billingName, input[name="billingName"]');
    if (await nameInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('  Filling cardholder name...');
      await nameInput.fill(card.name);
    }
  }

  // Step 6b: Fill ZIP code (required for US cards)
  const zipInput = page.locator('#billingPostalCode, input[name="billingPostalCode"], input[placeholder="ZIP"]');
  if (await zipInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    console.log('  Filling ZIP code...');
    await zipInput.fill(process.env.E2E_STRIPE_ZIP || '10001');
  }

  // Step 6c: Uncheck "Save my information" to avoid phone number requirement
  const saveCheckbox = page.getByRole('checkbox', { name: /save my information/i });
  if (await saveCheckbox.isVisible({ timeout: 2000 }).catch(() => false)) {
    if (await saveCheckbox.isChecked()) {
      console.log('  Unchecking "Save my information"...');
      await saveCheckbox.uncheck({ force: true });
    }
  }

  await page.screenshot({ path: 'test-results/stripe-checkout-filled.png' });
  console.log('  Card details filled');

  // Step 7: Click "Pay" button
  console.log('  Clicking Pay button...');
  const payButton = page.getByRole('button', { name: /^Pay$/i });
  await payButton.waitFor({ state: 'visible', timeout: 10_000 });
  await payButton.click();

  console.log('  Payment submitted, waiting for redirect...');
  // Wait for redirect back to our site
  await page.waitForURL(/nomadkaraoke\.com.*payment\/success|nomadkaraoke\.com.*\/app/, {
    timeout: 60_000,
  });
  await page.screenshot({ path: 'test-results/stripe-checkout-complete.png' });
  console.log('  Stripe Checkout complete — redirected to success page');
}

/**
 * Fill a card field by name. Tries direct input first, then iframe-based.
 *
 * Stripe Checkout card fields use IDs like #cardNumber, #cardExpiry, #cardCvc
 * when rendered as direct inputs, or are inside iframes when using Elements.
 */
async function fillCardField(page: Page, fieldName: string, value: string): Promise<void> {
  // Try 1: Direct input by ID
  const directInput = page.locator(`#${fieldName}`);
  if (await directInput.isVisible({ timeout: 3000 }).catch(() => false)) {
    await directInput.click();
    await directInput.type(value, { delay: 50 });
    return;
  }

  // Try 2: Direct input by data attribute
  const dataInput = page.locator(`[data-elements-stable-field-name="${fieldName}"]`);
  if (await dataInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await dataInput.click();
    await dataInput.type(value, { delay: 50 });
    return;
  }

  // Try 3: Input inside an iframe (Stripe Elements)
  // Find all iframes and try each one for the card input
  const iframes = page.locator('iframe');
  const iframeCount = await iframes.count();
  for (let i = 0; i < iframeCount; i++) {
    const frame = iframes.nth(i).contentFrame();
    // Look for inputs inside this iframe
    const input = frame.locator(`input[name="${fieldName}"], input[autocomplete*="cc"]`).first();
    if (await input.isVisible({ timeout: 1000 }).catch(() => false)) {
      await input.type(value, { delay: 50 });
      console.log(`    Found ${fieldName} in iframe ${i}`);
      return;
    }
  }

  throw new Error(`Could not find card field: ${fieldName} (tried direct input, data attribute, and ${iframeCount} iframes)`);
}
