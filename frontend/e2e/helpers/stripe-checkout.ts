// frontend/e2e/helpers/stripe-checkout.ts
import { Page } from '@playwright/test';

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
    const currentEmail = await emailInput.inputValue();
    if (!currentEmail) {
      console.log('  Stripe email field is empty — cannot fill without knowing email');
    } else {
      console.log(`  Stripe email pre-filled: ${currentEmail}`);
    }
  }

  // Diagnostic: dump the Stripe Checkout page structure to find correct selectors
  console.log('  Diagnosing Stripe Checkout page structure...');
  const diagInfo = await page.evaluate(() => {
    const info: string[] = [];
    // List all iframes
    const iframes = document.querySelectorAll('iframe');
    info.push(`Iframes (${iframes.length}):`);
    iframes.forEach((f, i) => {
      info.push(`  [${i}] title="${f.title}" name="${f.name}" src="${f.src?.substring(0, 100)}"`);
    });
    // List all visible inputs
    const inputs = document.querySelectorAll('input:not([type="hidden"])');
    info.push(`Inputs (${inputs.length}):`);
    inputs.forEach((inp, i) => {
      const el = inp as HTMLInputElement;
      info.push(`  [${i}] id="${el.id}" name="${el.name}" type="${el.type}" placeholder="${el.placeholder}" autocomplete="${el.autocomplete}"`);
    });
    // List all buttons/clickable elements with text
    const buttons = document.querySelectorAll('button, [role="button"], [role="radio"], [role="tab"]');
    info.push(`Buttons/Radio/Tabs (${buttons.length}):`);
    buttons.forEach((btn, i) => {
      info.push(`  [${i}] tag=${btn.tagName} role="${btn.getAttribute('role')}" text="${btn.textContent?.trim().substring(0, 60)}"`);
    });
    return info.join('\n');
  });
  console.log(diagInfo);

  // Try to click "Card" payment method — use the diagnostic output to find the right element
  // Stripe Checkout uses various structures. Try clicking elements containing "Card" text.
  const allClickable = page.locator('button, [role="button"], [role="radio"], [role="tab"], [role="option"], label, div[class*="PaymentMethod"]');
  const clickableCount = await allClickable.count();
  let cardClicked = false;
  for (let i = 0; i < clickableCount && !cardClicked; i++) {
    const text = await allClickable.nth(i).textContent().catch(() => '');
    if (text && /^\s*Card\s*$/i.test(text.replace(/visa|mastercard|amex|discover/gi, '').trim())) {
      await allClickable.nth(i).click();
      console.log(`  Clicked Card element at index ${i}`);
      cardClicked = true;
    }
  }
  if (!cardClicked) {
    // Try clicking by exact text "Card" anywhere on the page
    const cardText = page.getByText('Card', { exact: true }).first();
    if (await cardText.isVisible({ timeout: 2000 }).catch(() => false)) {
      await cardText.click();
      console.log('  Clicked Card by exact text match');
      cardClicked = true;
    }
  }
  if (!cardClicked) {
    console.log('  WARNING: Could not click Card payment method — trying to proceed anyway');
  }

  // Wait for card fields to render
  await page.waitForTimeout(3000);
  await page.screenshot({ path: 'test-results/stripe-card-selected.png' });

  // Re-diagnose after clicking Card to see what appeared
  console.log('  Post-card-click diagnosis...');
  const postDiag = await page.evaluate(() => {
    const iframes = document.querySelectorAll('iframe');
    const inputs = document.querySelectorAll('input:not([type="hidden"])');
    return `Iframes: ${iframes.length}, Inputs: ${inputs.length}`;
  });
  console.log(`  ${postDiag}`);

  // Fill card number — Stripe may use direct inputs or iframe-embedded inputs
  console.log('  Filling card number...');
  // First check if Stripe uses a single unified card input (newer Checkout versions)
  const cardNumberInput = page.locator('#cardNumber, [data-elements-stable-field-name="cardNumber"], input[name="cardNumber"]').first();
  if (await cardNumberInput.isVisible({ timeout: 5000 }).catch(() => false)) {
    console.log('  Found direct card number input');
    await cardNumberInput.click();
    await cardNumberInput.type(card.number, { delay: 50 });
  } else {
    // Fall back to iframe-based card input
    console.log('  Trying iframe-based card input...');
    await fillStripeField(
      page,
      'iframe[title*="card number" i], iframe[name*="cardNumber" i], iframe[title*="Secure card" i]',
      'input[name="cardnumber"], input[name="number"], input[autocomplete="cc-number"]',
      card.number,
    );
  }

  // Fill expiry
  console.log('  Filling card expiry...');
  const expiryInput = page.locator('#cardExpiry, [data-elements-stable-field-name="cardExpiry"], input[name="cardExpiry"]').first();
  if (await expiryInput.isVisible({ timeout: 3000 }).catch(() => false)) {
    await expiryInput.click();
    await expiryInput.type(card.expiry, { delay: 50 });
  } else {
    await fillStripeField(
      page,
      'iframe[title*="expir" i], iframe[name*="cardExpiry" i], iframe[title*="Secure card" i]',
      'input[name="exp-date"], input[name="expiry"], input[autocomplete="cc-exp"]',
      card.expiry,
    );
  }

  // Fill CVC
  console.log('  Filling CVC...');
  const cvcInput = page.locator('#cardCvc, [data-elements-stable-field-name="cardCvc"], input[name="cardCvc"]').first();
  if (await cvcInput.isVisible({ timeout: 3000 }).catch(() => false)) {
    await cvcInput.click();
    await cvcInput.type(card.cvc, { delay: 50 });
  } else {
    await fillStripeField(
      page,
      'iframe[title*="cvc" i], iframe[title*="security" i], iframe[name*="cardCvc" i]',
      'input[name="cvc"], input[autocomplete="cc-csc"]',
      card.cvc,
    );
  }

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
