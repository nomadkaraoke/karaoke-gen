/**
 * Email-code sign-in (POST /api/users/auth/send-code + verify-code), the login
 * the mobile app uses. API-level here: there is no web UI for it yet, and the
 * mobile Maestro flows drive the same endpoints against the same stack.
 */
import { test, expect } from '@playwright/test';
import { API_URL, uniqueEmail, waitForEmail } from './stack';

test('a new user signs in with an emailed 6-digit code and gets the welcome credit', async ({ request }) => {
  const email = uniqueEmail('code');

  const sent = await request.post(`${API_URL}/api/users/auth/send-code`, { data: { email, locale: 'en' } });
  expect(sent.status()).toBe(202);

  const mail = await waitForEmail(request, email, /.+/);
  expect(mail.codes, 'one 6-digit code in the email').toHaveLength(1);
  // The same email also carries a normal magic link.
  expect(mail.links.some((l) => l.includes('/auth/verify?token='))).toBe(true);

  const wrong = await request.post(`${API_URL}/api/users/auth/verify-code`, {
    data: { email, code: mail.codes[0] === '000000' ? '111111' : '000000' },
  });
  expect(wrong.status()).toBe(401);

  const verified = await request.post(`${API_URL}/api/users/auth/verify-code`, {
    data: { email, code: mail.codes[0] },
  });
  expect(verified.status()).toBe(200);
  const body = await verified.json();
  expect(body.session_token).toBeTruthy();
  expect(body.user.email).toBe(email);
  expect(body.credits_granted).toBe(1);

  const me = await request.get(`${API_URL}/api/users/me`, {
    headers: { Authorization: `Bearer ${body.session_token}` },
  });
  expect(me.status()).toBe(200);

  // Codes are single-use.
  const replay = await request.post(`${API_URL}/api/users/auth/verify-code`, {
    data: { email, code: mail.codes[0] },
  });
  expect(replay.status()).toBe(401);
});

test('five wrong guesses lock the code', async ({ request }) => {
  const email = uniqueEmail('lock');
  await request.post(`${API_URL}/api/users/auth/send-code`, { data: { email } });
  const mail = await waitForEmail(request, email, /.+/);
  const wrongCode = mail.codes[0] === '000000' ? '111111' : '000000';

  const statuses: number[] = [];
  for (let i = 0; i < 6; i++) {
    const r = await request.post(`${API_URL}/api/users/auth/verify-code`, { data: { email, code: wrongCode } });
    statuses.push(r.status());
  }
  expect(statuses.slice(0, 5)).toEqual([401, 401, 401, 401, 401]);
  expect(statuses[5]).toBe(429);

  // Even the right code no longer works once the code is burnt.
  const right = await request.post(`${API_URL}/api/users/auth/verify-code`, { data: { email, code: mail.codes[0] } });
  expect([401, 429]).toContain(right.status());
});
