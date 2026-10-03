// frontend/e2e/self-healing/units.spec.ts — pure-function tests (no browser page needed)
import { test, expect } from '@playwright/test';
import { frameUrlKey, substitutePlaceholders } from '../helpers/self-healing/actions';
import { buildPrompt, checkGuards, toTargetSpec } from '../helpers/self-healing/llm-agent';
import { MAX_LEARNED_PER_STEP, emptyFile, mergeLearned, variantId } from '../helpers/self-healing/learned-store';
import { redact } from '../helpers/self-healing/observe';
import { isSuccessRedirect } from '../helpers/stripe-checkout';
import type { Action } from '../helpers/self-healing/types';

const secrets = { CARD_NUMBER: '4242424242424242', CARD_EXPIRY: '12/34', CARD_CVC: '123', CARDHOLDER_NAME: 'Fixture Tester', POSTAL_CODE: '10001' };

test.describe('redact', () => {
  test('blanks textbox values and PAN-shaped digit runs, keeps structure', () => {
    const snap = [
      '- textbox "Card number": 4242 4242 4242 4242',
      '- textbox "Expiration" [active]: 12 / 34',
      '- textbox "CVC": 123',
      '- textbox "Name on card"',
      '- text: Total due $0.50',
      '- paragraph: ref 4242-4242-4242-4242',
      '- generic: Fixture Tester',
    ].join('\n');
    const out = redact(snap, secrets);
    expect(out).not.toMatch(/4242/);
    expect(out).not.toContain('12 / 34');
    expect(out).not.toContain(': 123');
    expect(out).not.toContain('Fixture Tester');
    expect(out).toContain('- textbox "Card number": [value redacted]');
    expect(out).toContain('- textbox "Name on card"');
    expect(out).toContain('$0.50');
  });
});

test.describe('checkGuards', () => {
  const step = { allowSubmit: false, placeholders: ['CARD_NUMBER'] };
  const click = (name: string) => ({ type: 'click' as const, target: { by: 'role' as const, role: 'button', name } });

  test('blocks final Pay outside submit step but allows "Pay with card"', () => {
    expect(checkGuards(click('Pay'), step)).toMatch(/not allowed/);
    expect(checkGuards(click('Pay $0.50'), step)).toMatch(/not allowed/);
    expect(checkGuards(click('Pay with card'), step)).toBeNull();
    expect(checkGuards(click('Pay'), { ...step, allowSubmit: true })).toBeNull();
  });
  test('blocks express methods and the cancel/back link', () => {
    for (const n of ['Pay securely with Link', 'Pay with Klarna', 'Cash App Pay', 'Bank', 'Back to Nomad Karaoke']) {
      expect(checkGuards(click(n), { ...step, allowSubmit: true }), n).not.toBeNull();
    }
  });
  test('only allowed placeholders; no literal card-like digits', () => {
    const fill = (value: string) => ({ type: 'fill' as const, target: { by: 'label' as const, text: 'Card number' }, value });
    expect(checkGuards(fill('{{CARD_NUMBER}}'), step)).toBeNull();
    expect(checkGuards(fill('{{CARD_CVC}}'), step)).toMatch(/not available/);
    expect(checkGuards(fill('4242424242424242'), step)).toMatch(/literal digits/);
    expect(checkGuards(fill('United States'), step)).toBeNull();
  });
  test('rejects malformed targets', () => {
    expect(checkGuards({ type: 'click', target: { by: 'css' } }, step)).toMatch(/css/);
    expect(checkGuards({ type: 'click', target: { by: 'role' } }, step)).toMatch(/role/);
  });
});

test('toTargetSpec maps frameIndex to a stable frame key and drops empties', () => {
  const spec = toTargetSpec({ frameIndex: 1, by: 'role', role: 'tab', name: 'Card', css: '' }, ['checkout.stripe.com/c/pay/x', 'js.stripe.com/v3/inner.html']);
  expect(spec).toEqual({ frameUrlIncludes: 'js.stripe.com/v3/inner.html', by: 'role', role: 'tab', name: 'Card' });
});

test('frameUrlKey strips query and hash (session ids)', () => {
  expect(frameUrlKey('https://js.stripe.com/v3/elements-inner.html?id=abc#x')).toBe('js.stripe.com/v3/elements-inner.html');
});

test('substitutePlaceholders substitutes known and throws on unknown', () => {
  expect(substitutePlaceholders('{{CARD_CVC}}', secrets)).toBe('123');
  expect(() => substitutePlaceholders('{{NOPE}}', secrets)).toThrow(/Unknown placeholder/);
});

test('buildPrompt contains goal/history but never raw secrets', () => {
  const prompt = buildPrompt({
    stepId: 'fillCardNumber',
    goal: 'Type "{{CARD_NUMBER}}"',
    allowSubmit: false,
    placeholders: ['CARD_NUMBER'],
    history: ['FAILED: x'],
    observation: { url: 'checkout.stripe.com/c/pay', text: '- textbox "Card number": [value redacted]', frameKeys: [] },
    escalation: 0,
  });
  expect(prompt).toContain('{{CARD_NUMBER}}');
  expect(prompt).toContain('FAILED: x');
  expect(prompt).toContain('Final Pay/submit button allowed in this step: NO');
});

test.describe('mergeLearned', () => {
  const a1: Action[] = [{ type: 'click', target: { by: 'role', role: 'tab', name: 'Card' } }];
  const a2: Action[] = [{ type: 'click', target: { by: 'text', text: 'Card', exact: true } }];

  test('adds newest-first, dedupes identical recipes, is pure', () => {
    const base = emptyFile();
    const r1 = mergeLearned(base, [{ stepId: 'selectCard', actions: a1, model: 'm' }], { now: 't1' });
    expect(r1.changed).toBe(true);
    expect(base.steps).toEqual({});
    const r2 = mergeLearned(r1.file, [{ stepId: 'selectCard', actions: a1 }], { now: 't2' });
    expect(r2.changed).toBe(false);
    const r3 = mergeLearned(r2.file, [{ stepId: 'selectCard', actions: a2 }], { now: 't3' });
    expect(r3.file.steps.selectCard.map((v) => v.id)).toEqual([variantId(a2), variantId(a1)]);
  });

  test(`caps each step at ${MAX_LEARNED_PER_STEP}`, () => {
    let file = emptyFile();
    for (let i = 0; i < MAX_LEARNED_PER_STEP + 3; i++) {
      file = mergeLearned(file, [{ stepId: 's', actions: [{ type: 'click', target: { by: 'text', text: `v${i}` } }] }]).file;
    }
    expect(file.steps.s).toHaveLength(MAX_LEARNED_PER_STEP);
    expect(file.steps.s[0].actions[0].target.text).toBe(`v${MAX_LEARNED_PER_STEP + 2}`);
  });

  test('skips empty recipes', () => {
    expect(mergeLearned(emptyFile(), [{ stepId: 's', actions: [] }]).changed).toBe(false);
  });
});

test('isSuccessRedirect accepts success/app URLs but not the cancel link', () => {
  expect(isSuccessRedirect('https://gen.nomadkaraoke.com/payment/success?session_id=x')).toBe(true);
  expect(isSuccessRedirect('https://gen.nomadkaraoke.com/en/app')).toBe(true);
  expect(isSuccessRedirect('https://gen.nomadkaraoke.com?cancelled=true')).toBe(false);
  expect(isSuccessRedirect('https://checkout.stripe.com/c/pay/x')).toBe(false);
});
