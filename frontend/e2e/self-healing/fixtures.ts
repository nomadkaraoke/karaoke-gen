// frontend/e2e/self-healing/fixtures.ts
import type { Page } from '@playwright/test';
import type { LlmDecision, Planner, PlannerInput } from '../helpers/self-healing/llm-agent';

export const CHECKOUT_URL = 'https://checkout.stripe.com/c/pay/cs_test_fixture#frag';
export const ELEMENTS_URL = 'https://js.stripe.com/v3/elements-inner-payment.html?id=abc';
export const SUCCESS_URL = 'https://gen.nomadkaraoke.com/payment/success?session_id=cs_test_fixture';

export const TEST_CARD = {
  E2E_STRIPE_CARD_NUMBER: '4242424242424242',
  E2E_STRIPE_CARD_EXPIRY: '12/34',
  E2E_STRIPE_CARD_CVC: '123',
  E2E_STRIPE_CARDHOLDER_NAME: 'Fixture Tester',
  E2E_STRIPE_ZIP: '10001',
};

export function setCardEnv(): void {
  Object.assign(process.env, TEST_CARD);
}

const cardFields = `
  <div id="card-form" style="display:none">
    <label>Card number <input id="cardNumber" autocomplete="cc-number" placeholder="1234 1234 1234 1234"></label>
    <label>Expiration <input id="cardExpiry" autocomplete="cc-exp" placeholder="MM / YY"></label>
    <label>CVC <input id="cardCvc" autocomplete="cc-csc"></label>
    <label>Name on card <input id="billingName" autocomplete="cc-name"></label>
    <label>ZIP code <input id="billingPostalCode" autocomplete="postal-code"></label>
  </div>`;

/**
 * Layout "oct2026": plain "Card" label with a visually-hidden "Pay with card"
 * button — the layout that broke run #192. Builtins handle it.
 */
export const ELEMENTS_OCT2026 = `<!doctype html><html><body>
  <div class="accordion">
    <div class="item" style="position:relative">
      <span>Card</span>
      <button aria-label="Pay with card" style="position:absolute;inset:0;opacity:0" onclick="document.getElementById('card-form').style.display='block'"></button>
    </div>
    <div class="item"><span>Klarna</span></div>
  </div>
  ${cardFields}
  <button id="pay" onclick="window.top.postMessage('pay','*')">Pay</button>
</body></html>`;

/**
 * Layout "future": a chooser NO builtin knows — a tab named "Credit or debit".
 * Only the LLM fallback (stubbed here) can get past it.
 */
export const ELEMENTS_FUTURE = `<!doctype html><html><body>
  <div role="tablist">
    <div role="tab" tabindex="0" aria-selected="false" onclick="document.getElementById('card-form').style.display='block';this.setAttribute('aria-selected','true')">Credit or debit</div>
    <div role="tab" tabindex="0" aria-selected="false">Bank</div>
  </div>
  ${cardFields}
  <button id="pay" onclick="window.top.postMessage('pay','*')">Pay</button>
</body></html>`;

const TOP_PAGE = `<!doctype html><html><body>
  <a href="https://gen.nomadkaraoke.com?cancelled=true">Back to Nomad Karaoke</a>
  <h2>Pay Nomad Karaoke</h2>
  <iframe src="${ELEMENTS_URL}" style="width:600px;height:500px"></iframe>
  <script>
    window.addEventListener('message', (e) => {
      if (e.data === 'pay') window.location.href = '${SUCCESS_URL}';
    });
  </script>
</body></html>`;

/** Serve a fake Stripe Checkout (top page + Payment Element iframe) and our success page. */
export async function serveCheckout(page: Page, elementsHtml: string): Promise<void> {
  await page.route('https://checkout.stripe.com/**', (r) => r.fulfill({ contentType: 'text/html', body: TOP_PAGE }));
  await page.route('https://js.stripe.com/**', (r) => r.fulfill({ contentType: 'text/html', body: elementsHtml }));
  await page.route('https://gen.nomadkaraoke.com/**', (r) =>
    r.fulfill({ contentType: 'text/html', body: '<h1>Payment successful</h1>' })
  );
}

/** Planner stub: returns scripted decisions in order and records every prompt input. */
export class ScriptedPlanner implements Planner {
  readonly inputs: PlannerInput[] = [];
  constructor(private readonly script: Array<LlmDecision | ((input: PlannerInput) => LlmDecision)>) {}
  async decide(input: PlannerInput) {
    this.inputs.push(input);
    const next = this.script.shift();
    const decision = typeof next === 'function' ? next(input) : next ?? { thought: 'out of script', status: 'give_up' as const };
    return { decision, model: 'stub-model' };
  }
}

export function elementsFrameIndex(input: PlannerInput): number {
  const idx = input.observation.frameKeys.findIndex((k) => k.includes('js.stripe.com'));
  if (idx < 0) throw new Error('elements frame not in observation');
  return idx;
}
