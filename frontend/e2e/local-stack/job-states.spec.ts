/**
 * How the job list presents jobs in each state, using seeded scenarios: a job
 * made through the real routes and walked by the fake workers to the state.
 */
import { test, expect } from '@playwright/test';
import { scenario, signInAs, waitForJobStatus } from './stack';

function jobCard(page: import('@playwright/test').Page, jobId: string) {
  return page.locator('[class*="rounded-lg"][class*="border"]').filter({ hasText: jobId.slice(0, 8) }).first();
}

test('a song longer than estimated asks to confirm the extra credit, then carries on', async ({ page, request }) => {
  const user = await scenario(request, 'job_awaiting_duration_confirm', { credits: 2 });
  await signInAs(page, user);
  await page.goto('/en/app');

  const card = jobCard(page, user.job_id!);
  await card.getByRole('button', { name: /confirm cost/i }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(/longer than expected/i)).toBeVisible();
  await dialog.getByRole('button', { name: /confirm & start/i }).click();

  await waitForJobStatus(request, user.session_token, user.job_id!, 'awaiting_review', 120_000);
  const me = await (await request.get('/api/users/me', {
    headers: { Authorization: `Bearer ${user.session_token}` },
  })).json();
  expect((me.user ?? me).credits).toBe(0); // 1 at creation + 1 for the extra 10 minutes
});

test('a failed job says so on its card', async ({ page, request }) => {
  const user = await scenario(request, 'job_failed');
  await signInAs(page, user);
  await page.goto('/en/app');
  await expect(jobCard(page, user.job_id!).getByText(/failed/i).first()).toBeVisible();
});

test('a finished job offers its downloads', async ({ page, request }) => {
  const user = await scenario(request, 'job_complete');
  await signInAs(page, user);
  await page.goto('/en/app');
  const card = jobCard(page, user.job_id!);
  await expect(card.getByText(/720p/i).first()).toBeVisible();
});

test('a job awaiting review opens straight into the lyrics review screen', async ({ page, request }) => {
  const user = await scenario(request, 'job_awaiting_review');
  await signInAs(page, user);
  await page.goto(`/en/app/jobs#/${user.job_id}/review`);
  await expect(page.getByRole('button', { name: /preview video/i })).toBeVisible();
  await expect(page.getByText(/This is a fake song for testing/i).first()).toBeVisible();
});
