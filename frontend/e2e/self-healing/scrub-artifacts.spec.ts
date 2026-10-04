// frontend/e2e/self-healing/scrub-artifacts.spec.ts — tests for e2e/scripts/scrub-artifacts.mjs
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

let scrub: any;
test.beforeAll(async () => {
  scrub = await import('../scripts/scrub-artifacts.mjs');
});

const CARD = '4242424242424242';
const ADMIN = 'admin-token-0123456789abcdef';
const SECRETS = [CARD, ADMIN, '12/34', '123'];

test('scrubText removes literal secrets, grouped card numbers and PAN-shaped digits', () => {
  const out = scrub.scrubText(`X-Admin-Token: ${ADMIN}\ncard 4242 4242 4242 4242 and 4242-4242-4242-4242 and 5555555555554444`, SECRETS);
  expect(out).not.toContain(ADMIN);
  expect(out).not.toMatch(/4242/);
  expect(out).not.toContain('5555555555554444');
  expect(out).toContain('X-Admin-Token: [redacted]');
});

test('short secrets (CVC/expiry) are not scrubbed globally, only in inputs with --blank-inputs', () => {
  const text = [
    'took 123ms',
    '<input id="cardCvc" value="123"><input id="exp" value=\'12/34\'>',
    '- textbox "Credit or debit card CVC/CVV" [ref=e162]:',
    '  - /placeholder: CVC',
    '  - text: "123"',
    '- textbox "Expiration" [ref=e157]: 12 / 34',
  ].join('\n');
  expect(scrub.scrubText(text, SECRETS)).toContain('value="123"');
  const blanked = scrub.scrubText(text, SECRETS, { blankInputs: true });
  expect(blanked).toContain('took 123ms');
  expect(blanked).not.toContain('value="123"');
  expect(blanked).not.toContain("12/34");
  expect(blanked).toContain('- text: [value redacted]');
  expect(blanked).toContain('- textbox "Expiration" [ref=e157]: [value redacted]');
});

test('scrubPaths deletes traces/tokens/videos, scrubs text, leaves images', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scrub-'));
  const sub = path.join(dir, 'spec-chromium');
  fs.mkdirSync(path.join(dir, 'report', 'data'), { recursive: true });
  fs.mkdirSync(sub);
  fs.writeFileSync(path.join(sub, 'trace.zip'), 'zip');
  fs.writeFileSync(path.join(dir, 'report', 'data', 'abc123.zip'), 'zip');
  fs.writeFileSync(path.join(sub, 'video.webm'), 'webm');
  fs.writeFileSync(path.join(dir, 'e2e-session-token.txt'), 'tok');
  fs.writeFileSync(path.join(sub, 'error-context.md'), `- textbox "Card number" [ref=e1]:\n  - text: ${CARD}\n`);
  fs.writeFileSync(path.join(sub, 'shot.png'), 'png');

  const stats = scrub.scrubPaths([dir, path.join(dir, 'missing.log')], SECRETS, { dropVideos: true, blankInputs: true });

  expect(fs.existsSync(path.join(sub, 'trace.zip'))).toBe(false);
  expect(fs.existsSync(path.join(dir, 'report', 'data', 'abc123.zip'))).toBe(false);
  expect(fs.existsSync(path.join(sub, 'video.webm'))).toBe(false);
  expect(fs.existsSync(path.join(dir, 'e2e-session-token.txt'))).toBe(false);
  expect(fs.existsSync(path.join(sub, 'shot.png'))).toBe(true);
  expect(fs.readFileSync(path.join(sub, 'error-context.md'), 'utf8')).not.toContain(CARD);
  expect(stats).toEqual({ deleted: 4, scrubbed: 1, deletedUnscrubbable: 0 });
});

test('videos are kept without --drop-videos', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scrub-'));
  fs.writeFileSync(path.join(dir, 'video.webm'), 'webm');
  scrub.scrubPaths([dir], SECRETS);
  expect(fs.existsSync(path.join(dir, 'video.webm'))).toBe(true);
});
