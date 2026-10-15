/**
 * Buying credits through the (fake) Stripe Checkout. The checkout page is served
 * by the local stack, and paying it runs the same completion handler as the real
 * Stripe webhook before redirecting to /payment/success.
 */
import { test, expect } from '@playwright/test';
import { scenario, signInAs, waitForEmail } from './stack';

test('user with no credits buys a pack and sees the new balance', async ({ page, request }) => {
  const user = await scenario(request, 'user', { credits: 0 });
  await signInAs(page, user);

  await page.goto('/en/app?buy=1');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /^3\s*credits/i }).click();
  await dialog.getByRole('button', { name: /continue to payment/i }).click();

  await page.waitForURL(/\/api\/test\/fake-checkout\//);
  await expect(page.getByTestId('fake-checkout-product')).toHaveText(/3 Karaoke Credits/);
  await page.getByTestId('fake-checkout-pay').click();

  await page.waitForURL(/\/payment\/success/);
  await expect(page.getByRole('heading', { name: /payment successful/i })).toBeVisible();
  await expect(page.getByText(/credits available/i)).toBeVisible();
  await expect(page.getByText('3', { exact: true })).toBeVisible();

  await waitForEmail(request, user.email, /credits added/i);
});

test('cancelling checkout leaves the balance unchanged', async ({ page, request }) => {
  const user = await scenario(request, 'user', { credits: 0 });
  await signInAs(page, user);

  await page.goto('/en/app?buy=1');
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: /^1\s*credit/i }).click();
  await dialog.getByRole('button', { name: /continue to payment/i }).click();
  await page.waitForURL(/\/api\/test\/fake-checkout\//);
  await page.getByTestId('fake-checkout-cancel').click();

  await page.waitForURL(/cancelled=true/);
  const me = await request.get('/api/users/me', { headers: { Authorization: `Bearer ${user.session_token}` } });
  const body = await me.json();
  expect((body.user ?? body).credits).toBe(0);
});
