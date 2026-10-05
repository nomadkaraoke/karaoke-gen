// frontend/e2e/helpers/stripe-checkout.ts
import { Page, Frame, Locator, Response } from '@playwright/test';
import * as fs from 'fs';
import { GeminiPlanner, type Planner } from './self-healing/llm-agent';
import { redact } from './self-healing/observe';
import { SelfHealingRunner } from './self-healing/step-runner';
import type { CheckoutStep, LearnedVariantsFile, Secrets, StepContext } from './self-healing/types';

/**
 * Automate Stripe's hosted checkout page (checkout.stripe.com).
 *
 * ⚠️ Stripe changes this page's structure without notice — likely A/B testing
 * or staged rollouts, so BOTH old and new layouts can appear on any given day
 * (and can flip back). Observed variants (see git history for details):
 *   1. Accordion of *radios* (Card / Cash App / Klarna / Bank).      (Apr 2026)
 *   2. Card-only: card fields as direct textboxes, no chooser.        (#916, Aug 17)
 *   3. Chooser as *buttons*, whole Payment Element in a nested iframe. (#919, Aug 20)
 *   4. Required "Name on card" + "ZIP code" with new labels.          (#945, Aug 26)
 *   5. Plain "Card" label + visually-hidden "Pay with card" button.   (#1115, Oct 3)
 *
 * So the flow is a sequence of SELF-HEALING STEPS (see ./self-healing/). Each
 * step has a `verify` postcondition and is attempted with:
 *   1. learned variants — recipes an LLM discovered on earlier runs, auto-
 *      committed to self-healing/learned-variants.json by the daily workflow;
 *   2. the hand-written builtin variants below;
 *   3. an LLM fallback (Gemini) that drives the page one action at a time
 *      until `verify` passes. Card data never reaches the LLM: it uses
 *      `{{CARD_NUMBER}}`-style placeholders that we substitute locally, and
 *      observations are redacted + input-masked.
 * When the LLM heals a step and the purchase is confirmed server-side, the
 * daily workflow opens an auto-merging PR adding the recipe to
 * learned-variants.json — the next run replays it without the LLM.
 *
 * You can still add builtin variants by hand, but you shouldn't need to.
 *
 * A *declined card* is not a layout change: Stripe's confirm call returns a
 * `card_error`, and the submit step fails immediately with a message starting
 * `STRIPE_CARD_DECLINED` (no LLM retries, no further Pay clicks).
 *
 * Artifacts: test-results/ is uploaded from a PUBLIC repo, so every screenshot
 * here masks inputs and the DOM dump has field values redacted.
 *
 * Environment variables:
 *   E2E_STRIPE_CARD_NUMBER, E2E_STRIPE_CARD_EXPIRY, E2E_STRIPE_CARD_CVC,
 *   E2E_STRIPE_CARDHOLDER_NAME (optional), E2E_STRIPE_ZIP (optional)
 *   GEMINI_API_KEY — enables the LLM fallback (absent → deterministic only)
 *   E2E_SELF_HEAL_MODELS — comma-separated Gemini model ids, cheapest first
 *   E2E_SELF_HEAL_FORCE_LLM — comma-separated step ids to force through the LLM (testing)
 */

interface CardDetails {
  number: string;
  expiry: string;
  cvc: string;
  name: string;
  zip: string;
}

function getCardDetailsFromEnv(): CardDetails {
  const number = process.env.E2E_STRIPE_CARD_NUMBER;
  const expiry = process.env.E2E_STRIPE_CARD_EXPIRY;
  const cvc = process.env.E2E_STRIPE_CARD_CVC;

  if (!number || !expiry || !cvc) {
    throw new Error(
      'Missing Stripe card env vars: E2E_STRIPE_CARD_NUMBER, E2E_STRIPE_CARD_EXPIRY, E2E_STRIPE_CARD_CVC'
    );
  }

  // Default the name so a missing/empty secret can't leave the (now required)
  // "Name on card" field blank and get the Pay click rejected.
  return {
    number,
    expiry,
    cvc,
    name: process.env.E2E_STRIPE_CARDHOLDER_NAME || 'E2E Test',
    zip: process.env.E2E_STRIPE_ZIP || '10001',
  };
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
 * Accessible-name, placeholder and autocomplete metadata for each Stripe card
 * field, used to build robust locators that work whether Stripe renders the
 * field as a direct input or inside an iframe.
 */
const CARD_FIELD_META: Record<string, { name: RegExp; placeholder?: string; autocomplete: string }> = {
  cardNumber: { name: /card number/i, placeholder: '1234 1234 1234 1234', autocomplete: 'cc-number' },
  cardExpiry: { name: /expiration|expiry/i, placeholder: 'MM / YY', autocomplete: 'cc-exp' },
  cardCvc: { name: /^cvc$|security code/i, autocomplete: 'cc-csc' },
};

/**
 * Locator for a Stripe card field within a given frame. Matches by Stripe's
 * stable field-name attribute, element id, input name, autocomplete token,
 * placeholder, or accessible name — whichever the current layout uses.
 */
function cardFieldLocator(frame: Frame, fieldName: string): Locator {
  const meta = CARD_FIELD_META[fieldName];
  const selectors = [
    `[data-elements-stable-field-name="${fieldName}"]`,
    `#${fieldName}`,
    `input[name="${fieldName}"]`,
  ];
  if (meta?.autocomplete) selectors.push(`input[autocomplete="${meta.autocomplete}"]`);
  if (meta?.placeholder) selectors.push(`input[placeholder="${meta.placeholder}"]`);
  let loc = frame.locator(selectors.join(', '));
  if (meta) loc = loc.or(frame.getByRole('textbox', { name: meta.name }));
  return loc.first();
}

function nameFieldLocator(f: Frame): Locator {
  // Stripe has labelled this "Cardholder name", "Name on card", "Full name on
  // card"; placeholder "Full name on card" or "First and last name".
  return f
    .locator(
      '[data-elements-stable-field-name="billingName"], #billingName, input[name="billingName"], input[autocomplete="cc-name"], input[placeholder="Full name on card"], input[placeholder="First and last name"]'
    )
    .or(f.getByRole('textbox', { name: /name on card|cardholder name/i }));
}

function zipFieldLocator(f: Frame): Locator {
  // Accessible name is "ZIP code" (not exactly "ZIP") — don't anchor the regex.
  return f
    .locator(
      '[data-elements-stable-field-name="billingPostalCode"], #billingPostalCode, input[name="billingPostalCode"], input[autocomplete="postal-code"], input[placeholder="ZIP"]'
    )
    .or(f.getByRole('textbox', { name: /zip|postal code/i }));
}

async function anyVisible(page: Page, make: (f: Frame) => Locator, timeout = 1_500): Promise<boolean> {
  return (await locateVisibleInFrames(page, make, timeout)) !== null;
}

/** Builtin "fill" variant: locate with `make`, type `value`, record as lastFilled. */
function fillVariant(name: string, make: (f: Frame) => Locator, value: string, typed: boolean) {
  return {
    name,
    run: async (ctx: StepContext) => {
      const input = await locateVisibleInFrames(ctx.page, make, 8_000);
      if (!input) throw new Error(`${name}: field not found`);
      await input.click();
      if (typed) await input.pressSequentially(value, { delay: 50 });
      else await input.fill(value);
      ctx.lastFilled = input;
    },
  };
}

const digits = (s: string) => s.replace(/\D/g, '');

/**
 * Fill-step postcondition: the element filled during this step holds the
 * expected value AND looks like the intended field (so a fallback that typed
 * the card number into, say, a phone field can't pass).
 */
function filledVerify(expected: string, fieldHint: RegExp, compare: 'digits' | 'text') {
  return async (ctx: StepContext): Promise<boolean> => {
    const el = ctx.lastFilled;
    if (!el) return false;
    const value = await el.inputValue({ timeout: 1_000 }).catch(() => '');
    const ok = compare === 'digits' ? digits(value) === digits(expected) : value.trim() === expected.trim();
    if (!ok) return false;
    const descriptor = await el
      .evaluate((node) => {
        const i = node as HTMLInputElement;
        const labels = Array.from(i.labels || []).map((l) => l.textContent || '');
        return [i.id, i.name, i.autocomplete, i.placeholder, i.getAttribute('aria-label'), i.getAttribute('data-elements-stable-field-name'), ...labels].join(' ');
      })
      .catch(() => '');
    return fieldHint.test(descriptor);
  };
}

/**
 * Back on our site after paying. Must NOT match the cancel URL
 * (`gen.nomadkaraoke.com?cancelled=true`, Stripe's "Back" link).
 */
export function isSuccessRedirect(url: string): boolean {
  return /nomadkaraoke\.com.*(payment\/success|\/app)/.test(url) && !/cancelled=true/.test(url);
}

/** Rate-limit the server-side paid check (verify polls every 500ms). */
function throttled(check: () => Promise<boolean>, intervalMs = 5_000): () => Promise<boolean> {
  let last = 0;
  let lastResult = false;
  return async () => {
    if (lastResult || Date.now() - last < intervalMs) return lastResult;
    last = Date.now();
    lastResult = await check().catch(() => false);
    return lastResult;
  };
}

/** Marker the daily workflow greps for to report "card declined" instead of "checkout broken". */
export const CARD_DECLINED_MARKER = 'STRIPE_CARD_DECLINED';

/**
 * Turn a failed Stripe Checkout `/payment_pages/<id>/confirm` response body
 * into an abort reason — only for `card_error`s (declines, incorrect CVC,
 * expired card…), which no UI retry can fix. Other errors (e.g. a missing
 * required field) return null so the LLM fallback can still repair the form.
 */
export function cardErrorReason(status: number, body: unknown): string | null {
  const err = (body as { error?: Record<string, unknown> } | null)?.error;
  if (!err || err.type !== 'card_error') return null;
  const codes = [err.code, err.decline_code].filter(Boolean).join('/');
  return (
    `${CARD_DECLINED_MARKER}: Stripe rejected the test card (HTTP ${status}, ${codes || 'card_error'}): ` +
    `${err.message || 'no message'} — the issuer/card needs attention; this is not a Checkout layout change`
  );
}

export function isCheckoutConfirmUrl(url: string): boolean {
  try {
    return /\/v1\/payment_pages\/[^/]+\/confirm$/.test(new URL(url).pathname);
  } catch {
    return false;
  }
}

/** Screenshot with every input in every frame masked (artifacts are public). */
async function maskedScreenshot(page: Page, file: string): Promise<void> {
  await page
    .screenshot({
      path: file,
      mask: page.frames().filter((f) => !f.isDetached()).map((f) => f.locator('input, textarea, select')),
      maskColor: '#888888',
    })
    .catch(() => {});
}

function buildSteps(
  card: CardDetails,
  verifyPaid?: () => Promise<boolean>,
  cardError?: () => string | null
): CheckoutStep[] {
  const paidCheck = verifyPaid ? throttled(verifyPaid) : undefined;
  const cardNumberVisible = (ctx: StepContext) =>
    anyVisible(ctx.page, (f) => cardFieldLocator(f, 'cardNumber'));

  return [
    {
      id: 'selectCard',
      goal: 'Make the credit/debit CARD payment form visible (card number, expiry, CVC inputs). If payment methods are listed (Card, Cash App, Klarna, Bank…), select/expand "Card". Do not choose any other method and do not pay.',
      verify: cardNumberVisible,
      verifyTimeoutMs: 8_000,
      builtins: [
        {
          name: 'click Card radio/button/label',
          run: async ({ page }) => {
            // Older variants: radio or button named "Card". Oct 2026: plain
            // "Card" label over a visually-hidden "Pay with card" button.
            const choice = await locateVisibleInFrames(
              page,
              (f) =>
                f
                  .getByRole('radio', { name: /^(pay with )?card$/i })
                  .or(f.getByRole('button', { name: /^(pay with )?card$/i }))
                  .or(f.getByText('Card', { exact: true })),
              5_000
            );
            if (!choice) throw new Error('no Card chooser visible');
            await choice.click({ force: true });
          },
        },
        {
          name: 'dispatch click on hidden "Pay with card"',
          run: async ({ page }) => {
            let fired = false;
            for (const frame of page.frames()) {
              if (frame.isDetached()) continue;
              const btn = frame.getByRole('button', { name: /^pay with card$/i, includeHidden: true }).first();
              if (await btn.count().catch(() => 0)) {
                await btn.dispatchEvent('click');
                fired = true;
              }
            }
            if (!fired) throw new Error('no hidden "Pay with card" button');
          },
        },
      ],
    },
    {
      id: 'fillCardNumber',
      goal: 'Type the card number into the card number input using value "{{CARD_NUMBER}}".',
      placeholders: ['CARD_NUMBER'],
      verify: filledVerify(card.number, /card.?number|cc-number|1234 1234/i, 'digits'),
      builtins: [fillVariant('card number field', (f) => cardFieldLocator(f, 'cardNumber'), card.number, true)],
    },
    {
      id: 'fillCardExpiry',
      goal: 'Type the card expiry date into the expiration (MM / YY) input using value "{{CARD_EXPIRY}}".',
      placeholders: ['CARD_EXPIRY'],
      verify: filledVerify(card.expiry, /expir|cc-exp|mm ?\/ ?yy/i, 'digits'),
      builtins: [fillVariant('card expiry field', (f) => cardFieldLocator(f, 'cardExpiry'), card.expiry, true)],
    },
    {
      id: 'fillCardCvc',
      goal: 'Type the card security code into the CVC input using value "{{CARD_CVC}}".',
      placeholders: ['CARD_CVC'],
      verify: filledVerify(card.cvc, /cvc|cvv|security|cc-csc/i, 'digits'),
      builtins: [fillVariant('card CVC field', (f) => cardFieldLocator(f, 'cardCvc'), card.cvc, true)],
    },
    {
      id: 'fillCardholderName',
      goal: 'Type the cardholder name into the "Name on card"/cardholder-name input using value "{{CARDHOLDER_NAME}}".',
      placeholders: ['CARDHOLDER_NAME'],
      optional: true,
      verify: filledVerify(card.name, /name/i, 'text'),
      builtins: [fillVariant('cardholder name field', nameFieldLocator, card.name, false)],
    },
    {
      id: 'fillPostalCode',
      goal: 'Type the billing ZIP/postal code using value "{{POSTAL_CODE}}".',
      placeholders: ['POSTAL_CODE'],
      optional: true,
      verify: filledVerify(card.zip, /zip|postal/i, 'text'),
      builtins: [fillVariant('ZIP field', zipFieldLocator, card.zip, false)],
    },
    {
      id: 'uncheckSaveInfo',
      goal: 'Make sure "Save my information for faster checkout" (Stripe Link) is NOT checked.',
      optional: true,
      // Avoids Link's phone-number requirement.
      verify: async ({ page }) => {
        const cb = await locateVisibleInFrames(page, (f) => f.getByRole('checkbox', { name: /save my information/i }), 1_000);
        return !cb || !(await cb.isChecked().catch(() => false));
      },
      builtins: [
        {
          name: 'uncheck "Save my information"',
          run: async ({ page }) => {
            const cb = await locateVisibleInFrames(page, (f) => f.getByRole('checkbox', { name: /save my information/i }), 2_000);
            if (cb) await cb.uncheck({ force: true });
          },
        },
      ],
    },
    {
      id: 'submitPayment',
      goal: 'Submit the card payment. If the form shows validation errors or empty REQUIRED fields (e.g. name, ZIP/postal code, country), fix them first using the placeholders, then click the main Pay/submit button (NOT Link/Klarna/other express buttons).',
      allowSubmit: true,
      placeholders: ['CARD_NUMBER', 'CARD_EXPIRY', 'CARD_CVC', 'CARDHOLDER_NAME', 'POSTAL_CODE'],
      // Long: the Stripe→site redirect and webhook grant can each take a while,
      // and a premature LLM fallback risks a second submit.
      verifyTimeoutMs: 90_000,
      abortReason: cardError,
      verify: async ({ page }) => {
        if (isSuccessRedirect(page.url())) return true;
        return paidCheck ? paidCheck() : false;
      },
      builtins: [
        {
          name: 'click Pay',
          run: async ({ page }) => {
            const pay = await locateVisibleInFrames(page, (f) => f.getByRole('button', { name: /^Pay$/i }), 10_000);
            if (!pay) throw new Error('Pay button not visible');
            await pay.click();
          },
        },
      ],
    },
  ];
}

async function dumpFrames(page: Page, file: string, secrets: Secrets): Promise<void> {
  // page.content() only covers the main frame; the Payment Element usually
  // lives in a nested iframe, so dump every frame.
  const dumps: string[] = [];
  for (const frame of page.frames()) {
    if (frame.isDetached()) continue;
    const html = (await frame.content().catch(() => ''))
      // Typed card data lives in value="…" attributes.
      .replace(/(<input\b[^>]*?\bvalue=)("[^"]*"|'[^']*')/gi, '$1"[redacted]"');
    dumps.push(`<!-- ===== frame: ${frame.url()} ===== -->\n${redact(html, secrets)}`);
  }
  fs.mkdirSync('test-results', { recursive: true });
  fs.writeFileSync(file, dumps.join('\n\n'));
}

export const SELF_HEAL_ARTIFACT_DIR = 'test-results/self-heal';

export interface StripeCheckoutOptions {
  /**
   * Server-side "did the purchase land?" check (e.g. credit balance increased).
   * Used by the submit step's postcondition when the browser redirect is slow,
   * so the LLM fallback never re-submits a payment that already succeeded.
   */
  verifyPaid?: () => Promise<boolean>;
  /** Override the LLM planner (tests inject a stub; `null` disables the fallback). Default: Gemini from env. */
  planner?: Planner | null;
  /** Override learned variants (tests). Default: self-healing/learned-variants.json. */
  learned?: LearnedVariantsFile;
}

/**
 * Complete the Stripe Checkout page with card details from environment.
 *
 * @returns `redirected` — whether the browser was observed navigating back to
 *   our site. The redirect can be slow or dropped even when the charge succeeds,
 *   so callers should treat the *server-side* credit grant as the source of
 *   truth. `selfHeal` — call `selfHeal.writeArtifacts(SELF_HEAL_ARTIFACT_DIR,
 *   { flowVerified: true })` once the purchase is confirmed server-side so
 *   any LLM-healed steps get promoted to learned variants.
 */
export async function completeStripeCheckout(
  page: Page,
  opts: StripeCheckoutOptions = {}
): Promise<{ redirected: boolean; selfHeal: SelfHealingRunner }> {
  const card = getCardDetailsFromEnv();
  const secrets: Secrets = {
    CARD_NUMBER: card.number,
    CARD_EXPIRY: card.expiry,
    CARD_CVC: card.cvc,
    CARDHOLDER_NAME: card.name,
    POSTAL_CODE: card.zip,
  };
  const planner = opts.planner !== undefined ? opts.planner : GeminiPlanner.fromEnv();
  if (!planner) console.log('  (no LLM planner — GEMINI_API_KEY unset; self-healing fallback disabled)');
  const runner = new SelfHealingRunner(page, secrets, planner, opts.learned);

  // Watch Stripe's confirm call so a declined card fails fast with a clear reason
  // instead of sending the LLM fallback to "fix" a form that is already correct.
  let cardError: string | null = null;
  const onResponse = async (res: Response) => {
    if (res.status() < 400 || !isCheckoutConfirmUrl(res.url())) return;
    const body = await res.json().catch(() => null);
    const reason = cardErrorReason(res.status(), body);
    if (reason) {
      cardError = reason;
      console.log(`  ✘ ${reason}`);
    } else {
      console.log(`  ⚠️ Stripe confirm returned HTTP ${res.status()} (not a card error — self-healing continues)`);
    }
  };
  page.on('response', onResponse);

  console.log('  Waiting for Stripe Checkout page...');
  await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 });
  // Readiness: any of the Pay button, the card form or a Card chooser. Don't
  // insist on one — that's exactly what Stripe keeps changing.
  const ready = await locateVisibleInFrames(
    page,
    (f) =>
      f
        .getByRole('button', { name: /^Pay\b/i })
        .or(cardFieldLocator(f, 'cardNumber'))
        .or(f.getByText('Card', { exact: true })),
    30_000
  );
  await maskedScreenshot(page, 'test-results/stripe-checkout-loaded.png');
  console.log(ready ? '  Stripe Checkout loaded' : '  ⚠️ Stripe Checkout readiness signal not seen — continuing with self-healing steps');

  try {
    for (const step of buildSteps(card, opts.verifyPaid, () => cardError)) {
      const outcome = await runner.run(step);
      if (step.id === 'selectCard') await maskedScreenshot(page, 'test-results/stripe-card-selected.png');
      if (step.id === 'uncheckSaveInfo') await maskedScreenshot(page, 'test-results/stripe-checkout-filled.png');
      if (outcome.status === 'failed') {
        await maskedScreenshot(page, `test-results/stripe-step-failed-${step.id}.png`);
        await dumpFrames(page, 'test-results/stripe-checkout-dom.html', secrets);
        throw new Error(`Stripe Checkout step "${step.id}" failed: ${outcome.reason}`);
      }
    }
  } catch (e) {
    runner.writeArtifacts(SELF_HEAL_ARTIFACT_DIR, { flowVerified: false });
    throw e;
  } finally {
    page.off('response', onResponse);
  }

  const redirected = isSuccessRedirect(page.url());
  await maskedScreenshot(page, `test-results/stripe-checkout-${redirected ? 'complete' : 'no-redirect'}.png`);
  if (redirected) {
    console.log('  Stripe Checkout complete — redirected to our site');
  } else {
    console.warn('  ⚠️ Payment confirmed server-side but no browser redirect observed (slow/dropped redirect)');
  }
  return { redirected, selfHeal: runner };
}
