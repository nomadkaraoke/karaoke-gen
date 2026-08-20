// frontend/e2e/helpers/stripe-checkout.ts
import { Page, Frame, Locator } from '@playwright/test';

/**
 * Automate Stripe's hosted checkout page (checkout.stripe.com).
 *
 * ⚠️ Stripe changes this page's structure without notice — likely A/B testing
 * or staged rollouts, so BOTH the old and new layouts can appear on any given
 * day (and can flip back). We have observed at least three variants:
 *   1. Multi-method accordion of *radios* (Card / Cash App / Klarna / Bank),
 *      card fields inside iframes, Pay button in the top frame.   (Apr 2026)
 *   2. Single method (Card only): card fields render as *direct* textboxes in
 *      the top document, no chooser at all.                        (#916, Aug 17)
 *   3. Multi-method Payment Element where the chooser is *buttons* (not radios)
 *      and the whole element — chooser, card fields AND the "Pay" button — is
 *      rendered inside a nested same-origin iframe.                (Aug 19-20)
 *
 * DO NOT narrow this helper to a single layout — it must tolerate all of the
 * above. It does so by being layout-agnostic:
 *   - `page.getByRole(...)` only sees the top frame, so we search EVERY frame
 *     (top + nested iframes) for each element via `locateVisibleInFrames`.
 *   - The "Card" chooser is matched as a radio OR a button.
 *   - Card fields are matched whether direct inputs or iframe-nested.
 * If Stripe introduces yet another layout, extend the locators here rather than
 * assuming a single shape.
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
 * Poll every frame (top document + nested iframes) for the first locator that
 * is visible, returning it (or null on timeout). Playwright's Page-level
 * locators only see the top frame; Stripe's Payment Element lives in a nested
 * iframe, so we must look frame-by-frame.
 */
async function locateVisibleInFrames(
  page: Page,
  make: (frame: Frame) => Locator,
  timeout = 30_000
): Promise<Locator | null> {
  const deadline = Date.now() + timeout;
  do {
    for (const frame of page.frames()) {
      if (frame.isDetached()) continue;
      const loc = make(frame).first();
      // isVisible() rejects if the frame detaches mid-check — treat as "not here".
      if (await loc.isVisible({ timeout: 1_000 }).catch(() => false)) {
        return loc;
      }
    }
    await page.waitForTimeout(300);
  } while (Date.now() < deadline);
  return null;
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

  // The "Pay" button is one of the last elements to render and confirms the
  // Payment Element is interactive. It may be in the top frame (single-method
  // layout) or a nested iframe (multi-method layout). Match it exactly so the
  // "Pay with Link" / "Pay with Klarna" express buttons don't shadow it.
  const payButton = await locateVisibleInFrames(
    page,
    (f) => f.getByRole('button', { name: /^Pay$/i }),
    30_000
  );
  if (!payButton) {
    await page.screenshot({ path: 'test-results/stripe-no-pay-button.png' });
    throw new Error('Stripe "Pay" button not visible in any frame after 30s');
  }
  await page.screenshot({ path: 'test-results/stripe-checkout-loaded.png' });
  console.log('  Stripe Checkout loaded');

  // Step 2: Ensure the card fields are shown. In the single-method layout they
  // are already visible; in the multi-method accordion we must expand "Card"
  // (rendered as a button or radio depending on Stripe's variant).
  console.log('  Selecting Card payment method...');
  let cardNumberField = await locateVisibleInFrames(
    page,
    (f) => cardFieldLocator(f, 'cardNumber'),
    5_000
  );

  if (cardNumberField) {
    console.log('  Card fields already visible — no chooser to expand');
  } else {
    const cardChoice = await locateVisibleInFrames(
      page,
      (f) =>
        f
          .getByRole('radio', { name: /^Card$/i })
          .or(f.getByRole('button', { name: /^Card$/i })),
      5_000
    );
    if (cardChoice) {
      // force:true bypasses any overlay/accordion animation intercepting clicks.
      await cardChoice.click({ force: true }).catch(() => {});
      console.log('  Card payment method selected');
    } else {
      console.log('  No Card chooser found — waiting for card fields to render...');
    }
    cardNumberField = await locateVisibleInFrames(
      page,
      (f) => cardFieldLocator(f, 'cardNumber'),
      15_000
    );
    if (!cardNumberField) {
      console.log('  WARNING: card number field not visible after selecting Card');
    }
  }

  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'test-results/stripe-card-selected.png' });

  // Step 3-5: Fill card number, expiry, CVC
  console.log('  Filling card number...');
  await fillCardField(page, 'cardNumber', card.number);
  console.log('  Filling card expiry...');
  await fillCardField(page, 'cardExpiry', card.expiry);
  console.log('  Filling CVC...');
  await fillCardField(page, 'cardCvc', card.cvc);

  // Step 6: Fill cardholder name if the field exists (any frame)
  if (card.name) {
    const nameInput = await locateVisibleInFrames(
      page,
      (f) =>
        f
          .locator('#billingName, input[name="billingName"], input[placeholder="Full name on card"]')
          .or(f.getByRole('textbox', { name: /cardholder name/i })),
      2_000
    );
    if (nameInput) {
      console.log('  Filling cardholder name...');
      await nameInput.fill(card.name);
    }
  }

  // Step 6b: Fill ZIP code (required for US cards) — search any frame
  const zipInput = await locateVisibleInFrames(
    page,
    (f) =>
      f
        .locator('#billingPostalCode, input[name="billingPostalCode"], input[placeholder="ZIP"]')
        .or(f.getByRole('textbox', { name: /^zip$|postal code/i })),
    2_000
  );
  if (zipInput) {
    console.log('  Filling ZIP code...');
    await zipInput.fill(process.env.E2E_STRIPE_ZIP || '10001');
  }

  // Step 6c: Uncheck "Save my information" to avoid a phone-number requirement
  const saveCheckbox = await locateVisibleInFrames(
    page,
    (f) => f.getByRole('checkbox', { name: /save my information/i }),
    2_000
  );
  if (saveCheckbox && (await saveCheckbox.isChecked().catch(() => false))) {
    console.log('  Unchecking "Save my information"...');
    await saveCheckbox.uncheck({ force: true }).catch(() => {});
  }

  await page.screenshot({ path: 'test-results/stripe-checkout-filled.png' });
  console.log('  Card details filled');

  // Step 7: Click "Pay" button (re-locate in case the frame re-rendered)
  console.log('  Clicking Pay button...');
  const finalPay = await locateVisibleInFrames(
    page,
    (f) => f.getByRole('button', { name: /^Pay$/i }),
    10_000
  );
  if (!finalPay) {
    await page.screenshot({ path: 'test-results/stripe-no-pay-button-final.png' });
    throw new Error('Stripe "Pay" button not visible when ready to submit');
  }
  await finalPay.click();

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
 * input (single-payment-method layout) or inside an iframe.
 */
const CARD_FIELD_META: Record<string, { name: RegExp; placeholder?: string }> = {
  cardNumber: { name: /card number/i, placeholder: '1234 1234 1234 1234' },
  cardExpiry: { name: /expiration|expiry/i, placeholder: 'MM / YY' },
  cardCvc: { name: /^cvc$|security code/i },
};

/**
 * Locator for a Stripe card field within a given frame. Matches by Stripe's
 * stable field-name attribute, element id, input name, placeholder, or
 * accessible name — whichever the current Checkout layout uses.
 */
function cardFieldLocator(frame: Frame, fieldName: string): Locator {
  const meta = CARD_FIELD_META[fieldName];
  const selectors = [
    `[data-elements-stable-field-name="${fieldName}"]`,
    `#${fieldName}`,
    `input[name="${fieldName}"]`,
  ];
  if (meta?.placeholder) selectors.push(`input[placeholder="${meta.placeholder}"]`);
  let loc = frame.locator(selectors.join(', '));
  if (meta) loc = loc.or(frame.getByRole('textbox', { name: meta.name }));
  return loc.first();
}

/**
 * Fill a card field by name, searching every frame (top document + nested
 * iframes). Stripe renders card fields either as direct inputs (single-payment-
 * method layout) or inside the Payment Element iframe (multi-method layout).
 */
async function fillCardField(page: Page, fieldName: string, value: string): Promise<void> {
  const input = await locateVisibleInFrames(page, (f) => cardFieldLocator(f, fieldName), 15_000);
  if (!input) {
    throw new Error(`Could not find card field: ${fieldName} in any frame`);
  }
  await input.click();
  await input.type(value, { delay: 50 });
}
