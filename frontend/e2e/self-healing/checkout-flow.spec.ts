// frontend/e2e/self-healing/checkout-flow.spec.ts
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { completeStripeCheckout } from '../helpers/stripe-checkout';
import { emptyFile } from '../helpers/self-healing/learned-store';
import type { LearnedVariantsFile } from '../helpers/self-healing/types';
import {
  CARD_DECLINED_RESPONSE,
  CHECKOUT_URL,
  ELEMENTS_FUTURE,
  ELEMENTS_OCT2026,
  ScriptedPlanner,
  TEST_CARD,
  elementsFrameIndex,
  serveCheckout,
  setCardEnv,
} from './fixtures';

test.beforeAll(() => {
  setCardEnv();
  fs.mkdirSync('test-results', { recursive: true });
});

function tmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'selfheal-'));
}

test('known layout (Oct 2026 hidden "Pay with card") completes via builtins — no LLM call', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_OCT2026);
  await page.goto(CHECKOUT_URL);
  const planner = new ScriptedPlanner([]);

  const result = await completeStripeCheckout(page, { planner, learned: emptyFile() });

  expect(result.redirected).toBe(true);
  expect(planner.inputs).toHaveLength(0);
  const statuses = Object.fromEntries(result.selfHeal.outcomes.map((o) => [o.stepId, o.status]));
  expect(statuses).toMatchObject({
    selectCard: 'ok',
    fillCardNumber: 'ok',
    fillCardExpiry: 'ok',
    fillCardCvc: 'ok',
    fillCardholderName: 'ok',
    fillPostalCode: 'ok',
    submitPayment: 'ok',
  });
  const dir = tmpDir();
  expect(result.selfHeal.writeArtifacts(dir, { flowVerified: true }).learnedWritten).toBe(false);
});

test('unknown layout: LLM heals selectCard, recipe is learned, and the next run replays it without the LLM', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_FUTURE);
  await page.goto(CHECKOUT_URL);
  const planner = new ScriptedPlanner([
    (input) => ({
      thought: 'Card form is behind the "Credit or debit" tab',
      status: 'act',
      action: { type: 'click', target: { frameIndex: elementsFrameIndex(input), by: 'role', role: 'tab', name: 'Credit or debit' } },
    }),
  ]);

  const result = await completeStripeCheckout(page, { planner, learned: emptyFile() });
  expect(result.redirected).toBe(true);
  expect(planner.inputs).toHaveLength(1);
  expect(planner.inputs[0].stepId).toBe('selectCard');

  const healed = result.selfHeal.healed;
  expect(healed).toHaveLength(1);
  expect(healed[0].actions[0]).toEqual({
    type: 'click',
    target: { frameUrlIncludes: 'js.stripe.com/v3/elements-inner-payment.html', by: 'role', role: 'tab', name: 'Credit or debit' },
  });

  // Promotion only happens once the flow is verified.
  const unverifiedDir = tmpDir();
  expect(result.selfHeal.writeArtifacts(unverifiedDir, { flowVerified: false }).learnedWritten).toBe(false);
  expect(fs.existsSync(path.join(unverifiedDir, 'learned-variants.json'))).toBe(false);

  const dir = tmpDir();
  expect(result.selfHeal.writeArtifacts(dir, { flowVerified: true, runUrl: 'https://example/run/1' }).learnedWritten).toBe(true);
  const learned = JSON.parse(fs.readFileSync(path.join(dir, 'learned-variants.json'), 'utf8')) as LearnedVariantsFile;
  expect(learned.steps.selectCard).toHaveLength(1);
  expect(learned.steps.selectCard[0].runUrl).toBe('https://example/run/1');
  const report = JSON.parse(fs.readFileSync(path.join(dir, 'self-heal-report.json'), 'utf8'));
  expect(report.llmCalls).toBe(1);

  // Second run: same unknown layout, learned recipe replayed, LLM not consulted.
  const page2 = await page.context().newPage();
  await serveCheckout(page2, ELEMENTS_FUTURE);
  await page2.goto(CHECKOUT_URL);
  const planner2 = new ScriptedPlanner([]);
  const rerun = await completeStripeCheckout(page2, { planner: planner2, learned });
  expect(rerun.redirected).toBe(true);
  expect(planner2.inputs).toHaveLength(0);
  expect(rerun.selfHeal.outcomes.find((o) => o.stepId === 'selectCard')).toMatchObject({ status: 'ok', via: 'learned' });
});

test('no secrets reach the LLM — prompts use placeholders and observations are redacted', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_FUTURE);
  await page.goto(CHECKOUT_URL);
  // Force the LLM for a fill step that runs AFTER the card number was typed,
  // so the card number is present on the page while observing.
  const planner = new ScriptedPlanner([
    (input) => ({
      thought: 'open card tab',
      status: 'act',
      action: { type: 'click', target: { frameIndex: elementsFrameIndex(input), by: 'role', role: 'tab', name: 'Credit or debit' } },
    }),
    (input) => ({
      thought: 'fill expiry',
      status: 'act',
      action: { type: 'fill', target: { frameIndex: elementsFrameIndex(input), by: 'label', text: 'Expiration' }, value: '{{CARD_EXPIRY}}' },
    }),
  ]);
  process.env.E2E_SELF_HEAL_FORCE_LLM = 'fillCardExpiry';
  try {
    const result = await completeStripeCheckout(page, { planner, learned: emptyFile() });
    expect(result.redirected).toBe(true);
  } finally {
    delete process.env.E2E_SELF_HEAL_FORCE_LLM;
  }
  expect(planner.inputs.map((i) => i.stepId)).toEqual(['selectCard', 'fillCardExpiry']);
  const expiryInput = planner.inputs[1];
  const everything = JSON.stringify(expiryInput);
  expect(everything).not.toContain(TEST_CARD.E2E_STRIPE_CARD_NUMBER);
  expect(everything).not.toContain('4242 4242');
  expect(everything).not.toContain(TEST_CARD.E2E_STRIPE_CARDHOLDER_NAME);
  expect(expiryInput.placeholders).toEqual(['CARD_EXPIRY']);
});

test('guardrails: LLM may not click Pay outside the submit step or pick express methods; step fails cleanly', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_FUTURE);
  await page.goto(CHECKOUT_URL);
  const planner = new ScriptedPlanner([
    (input) => ({ thought: 'just pay', status: 'act', action: { type: 'click', target: { frameIndex: elementsFrameIndex(input), by: 'role', role: 'button', name: 'Pay', exact: true } } }),
    (input) => ({ thought: 'use bank', status: 'act', action: { type: 'click', target: { frameIndex: elementsFrameIndex(input), by: 'role', role: 'tab', name: 'Bank' } } }),
    // css target with no label — must be caught by the resolved-element guard
    (input) => ({ thought: 'pay via css', status: 'act', action: { type: 'click', target: { frameIndex: elementsFrameIndex(input), by: 'css', css: '#pay' } } }),
    { thought: 'cannot', status: 'give_up' },
    { thought: 'cannot', status: 'give_up' },
    { thought: 'cannot', status: 'give_up' },
  ]);

  await expect(completeStripeCheckout(page, { planner, learned: emptyFile() })).rejects.toThrow(/step "selectCard" failed/);
  expect(page.url()).toContain('checkout.stripe.com'); // never submitted
  const history = planner.inputs.at(-1)!.history.join('\n');
  expect(history).toContain('not allowed in this step');
  expect(history).toContain('alternative/express payment method');
  expect(history).toContain('the target element is the final Pay/submit button');
});

test('without a planner, an unknown layout fails with a clear reason (deterministic-only mode)', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_FUTURE);
  await page.goto(CHECKOUT_URL);
  await expect(completeStripeCheckout(page, { planner: null, learned: emptyFile() })).rejects.toThrow(/no LLM planner/);
  expect(fs.existsSync('test-results/stripe-checkout-dom.html')).toBe(true);
});

test('declined card fails fast with STRIPE_CARD_DECLINED — no LLM calls, no second Pay click', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_OCT2026, CARD_DECLINED_RESPONSE);
  let confirmCalls = 0;
  page.on('request', (r) => {
    if (r.url().endsWith('/confirm') && r.method() === 'POST') confirmCalls++;
  });
  await page.goto(CHECKOUT_URL);
  const planner = new ScriptedPlanner([]);

  const started = Date.now();
  await expect(completeStripeCheckout(page, { planner, learned: emptyFile() })).rejects.toThrow(
    /STRIPE_CARD_DECLINED: .*card_declined\/generic_decline.*Your card was declined/
  );
  // Without the abort the submit step would poll its full 90s verify, then hand off to the LLM.
  expect(Date.now() - started).toBeLessThan(60_000);
  expect(planner.inputs).toHaveLength(0);
  expect(confirmCalls).toBe(1);
});

test('failure artifacts never contain typed card data', async ({ page }) => {
  await serveCheckout(page, ELEMENTS_OCT2026, CARD_DECLINED_RESPONSE);
  await page.goto(CHECKOUT_URL);
  await expect(completeStripeCheckout(page, { planner: null, learned: emptyFile() })).rejects.toThrow(/STRIPE_CARD_DECLINED/);
  const dom = fs.readFileSync('test-results/stripe-checkout-dom.html', 'utf8');
  expect(dom).toContain('id="cardNumber"');
  for (const value of [TEST_CARD.E2E_STRIPE_CARD_NUMBER, TEST_CARD.E2E_STRIPE_CARD_EXPIRY, TEST_CARD.E2E_STRIPE_CARD_CVC]) {
    expect(dom).not.toContain(`value="${value}"`);
  }
  expect(dom).not.toContain(TEST_CARD.E2E_STRIPE_CARD_NUMBER);
});
