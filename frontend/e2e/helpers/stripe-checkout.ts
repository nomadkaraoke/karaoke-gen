// frontend/e2e/helpers/stripe-checkout.ts
import { Page, Locator } from '@playwright/test';

/**
 * Automate Stripe's hosted checkout page (checkout.stripe.com).
 *
 * Stripe Checkout's layout depends on how many payment methods are enabled:
 * - Multiple methods → an accordion of radio buttons (Card, Cash App Pay, …);
 *   "Card" must be selected to reveal the card fields (historically in iframes).
 * - Single method (Card only, current prod as of Aug 2026) → the card fields
 *   (number, expiry, CVC, name, ZIP) render directly with no radio to select.
 * Because the whole page is on checkout.stripe.com, card fields are direct
 * inputs rather than cross-origin iframes.
 * A "Pay" button sits at the bottom in both layouts.
 *
 * This helper handles both layouts: it only clicks the "Card" radio when one is
 * actually present, and fills fields via direct locators with an iframe fallback.
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

  // Step 2: Select "Card" payment method (only if a selector is present)
  // Stripe's Checkout layout varies with the number of enabled payment methods:
  //   - Multiple methods → an accordion of radio buttons; "Card" must be selected.
  //   - Single method (Card only) → card fields render directly, no radio exists.
  // As of Aug 2026 production serves the single-method layout: the card number,
  // expiry and CVC are shown immediately with no "Card" radio. Waiting on a radio
  // that never appears is what previously hung this step for 10s.
  console.log('  Selecting Card payment method...');
  const cardNumberField = cardFieldLocator(page, 'cardNumber');

  if (await cardNumberField.isVisible({ timeout: 5_000 }).catch(() => false)) {
    console.log('  Card fields already visible (single payment method) — no radio to select');
  } else {
    const cardRadio = page.getByRole('radio', { name: 'Card' });
    if (await cardRadio.count()) {
      // Accordion layout — bypass the overlay interception with force:true.
      await cardRadio.check({ force: true, timeout: 10_000 });
      console.log('  Card payment method selected via radio');
    } else {
      console.log('  No Card radio found — waiting for card fields to render...');
    }
    // Give Stripe a moment to render the card inputs (iframes or direct fields).
    await cardNumberField
      .waitFor({ state: 'visible', timeout: 15_000 })
      .catch(() => console.log('  WARNING: card number field not visible after selecting Card'));
  }

  await page.waitForTimeout(1000);
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
    const nameInput = page
      .locator('#billingName, input[name="billingName"], input[placeholder="Full name on card"]')
      .or(page.getByRole('textbox', { name: /cardholder name/i }))
      .first();
    if (await nameInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('  Filling cardholder name...');
      await nameInput.fill(card.name);
    }
  }

  // Step 6b: Fill ZIP code (required for US cards)
  const zipInput = page
    .locator('#billingPostalCode, input[name="billingPostalCode"], input[placeholder="ZIP"]')
    .or(page.getByRole('textbox', { name: /^zip$|postal code/i }))
    .first();
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
 * Accessible-name and placeholder metadata for each Stripe card field, used to
 * build robust locators that work whether Stripe renders the field as a direct
 * input (current single-payment-method layout) or inside an iframe.
 */
const CARD_FIELD_META: Record<string, { name: RegExp; placeholder?: string }> = {
  cardNumber: { name: /card number/i, placeholder: '1234 1234 1234 1234' },
  cardExpiry: { name: /expiration|expiry/i, placeholder: 'MM / YY' },
  cardCvc: { name: /^cvc$|security code/i },
};

/**
 * Locator for a Stripe card field rendered directly in the main frame (i.e. not
 * inside an iframe). Matches by Stripe's stable field-name attribute, element id,
 * input name, or placeholder — whichever the current Checkout layout uses.
 */
function cardFieldLocator(page: Page, fieldName: string): Locator {
  const meta = CARD_FIELD_META[fieldName];
  const selectors = [
    `[data-elements-stable-field-name="${fieldName}"]`,
    `#${fieldName}`,
    `input[name="${fieldName}"]`,
  ];
  if (meta?.placeholder) selectors.push(`input[placeholder="${meta.placeholder}"]`);
  return page.locator(selectors.join(', ')).first();
}

/**
 * Fill a card field by name. Tries direct inputs (id / stable-field-name /
 * placeholder / accessible name) first, then falls back to iframe-based inputs.
 *
 * Stripe Checkout renders card fields as direct inputs in the single-payment-
 * method layout, or inside iframes when using the classic Elements accordion.
 */
async function fillCardField(page: Page, fieldName: string, value: string): Promise<void> {
  // Try 1: Direct input by stable-field-name / id / name / placeholder
  const directInput = cardFieldLocator(page, fieldName);
  if (await directInput.isVisible({ timeout: 3000 }).catch(() => false)) {
    await directInput.click();
    await directInput.type(value, { delay: 50 });
    return;
  }

  // Try 2: Direct input by accessible name (aria-label)
  const meta = CARD_FIELD_META[fieldName];
  if (meta) {
    const roleInput = page.getByRole('textbox', { name: meta.name }).first();
    if (await roleInput.isVisible({ timeout: 2000 }).catch(() => false)) {
      await roleInput.click();
      await roleInput.type(value, { delay: 50 });
      return;
    }
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
