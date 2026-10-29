// frontend/e2e/self-healing/page-activity.spec.ts — tests for e2e/helpers/page-activity.ts
// (lives in this offline suite because it runs in CI with a real browser and no network).
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { recordPageActivity, sanitizeUrl } from '../helpers/page-activity';

test('sanitizeUrl keeps origin + path and drops query strings and fragments', () => {
  expect(sanitizeUrl('https://gen.example.test/auth/verify?token=secret-magic#x')).toBe('https://gen.example.test/auth/verify');
  expect(sanitizeUrl('https://storage.example.test/b/o.png?X-Goog-Signature=abc')).toBe('https://storage.example.test/b/o.png');
  expect(sanitizeUrl('data:image/png;base64,AAAA')).toBe('data:…');
  expect(sanitizeUrl('not a url?token=x')).toBe('not a url');
});

test('records navigations, requests, failures, console + page errors, and in-flight requests', async ({ page }, testInfo) => {
  const ORIGIN = 'https://portal.example.test';
  await page.route(`${ORIGIN}/app`, (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<html><body><script>
        fetch('${ORIGIN}/api/config?token=secret-magic');
        fetch('${ORIGIN}/api/broken').catch(() => {});
        fetch('${ORIGIN}/api/hangs');
        console.error('boom from console');
        setTimeout(() => { throw new Error('uncaught kaboom'); }, 0);
      </script></body></html>`,
    }),
  );
  await page.route(`${ORIGIN}/api/config*`, (route) => route.fulfill({ status: 200, body: '{}' }));
  await page.route(`${ORIGIN}/api/broken`, (route) => route.abort('failed'));
  await page.route(`${ORIGIN}/api/hangs`, () => { /* never answered: stays in flight */ });

  const activity = recordPageActivity(page);
  await page.goto(`${ORIGIN}/app`);
  await expect.poll(() => activity.lines().join('\n')).toContain('PAGEERROR');
  await expect.poll(() => activity.lines().join('\n')).toContain('REQ 200 GET https://portal.example.test/api/config');

  const text = activity.lines().join('\n');
  expect(text).toContain('NAV      https://portal.example.test/app');
  expect(text).toMatch(/REQFAIL {2}GET https:\/\/portal\.example\.test\/api\/broken/);
  expect(text).toContain('CONSOLE.error boom from console');
  expect(text).toContain('PAGEERROR Error: uncaught kaboom');
  expect(text).not.toContain('secret-magic');
  expect(activity.pending().join('\n')).toContain('GET https://portal.example.test/api/hangs');

  // Passing test: nothing written.
  await activity.writeOnFailure(testInfo);
  expect(fs.existsSync(testInfo.outputPath('page-activity.log'))).toBe(false);

  // Failed test: log written with the in-flight section.
  const failed = Object.create(testInfo, { status: { value: 'timedOut' } });
  await activity.writeOnFailure(failed);
  const log = fs.readFileSync(testInfo.outputPath('page-activity.log'), 'utf8');
  expect(log).toContain('IN FLIGHT AT FAILURE (1):');
  expect(log).toContain('/api/hangs');
  expect(log).not.toContain('secret-magic');
});
