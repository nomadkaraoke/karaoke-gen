/**
 * The whole customer journey through the real UI and the real backend, with
 * fake workers: sign up by magic link → make a video → review lyrics → pick the
 * instrumental → download the finished video.
 */
import { test, expect } from '@playwright/test';
import { setFakeConfig, uniqueEmail, waitForEmail, waitForJobStatus } from './stack';

const SONG = { artist: 'Local Stack', title: 'Full Journey' };

test('new user signs up, makes a karaoke video, reviews it and downloads it', async ({ page, request }) => {
  const email = uniqueEmail('journey');
  // Small delay so the job visibly moves through its states; human review.
  await setFakeConfig(request, email, { step_delay_seconds: 0.3, auto_approve: false });

  await test.step('sign up with a magic link', async () => {
    await page.goto('/en');
    await page.getByRole('button', { name: /sign up free/i }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByPlaceholder('you@example.com').fill(email);
    await page.getByRole('button', { name: /send sign-in link/i }).click();
    await expect(page.getByText(/check your email/i)).toBeVisible();

    const mail = await waitForEmail(request, email, /sign in/i);
    const link = mail.links.find((l) => l.includes('/auth/verify?token='));
    expect(link, 'magic link in email').toBeTruthy();
    await page.goto(link!);
    await page.getByRole('button', { name: /complete sign-?in/i }).click();

    // New users get the welcome-credit interstitial.
    await page.getByRole('button', { name: /start creating|go to dashboard|explore the app/i }).click();
    await page.waitForURL(/\/app/);
    await expect(page.getByText(/1\s+credit/i).first()).toBeVisible();
  });

  let jobId = '';
  await test.step('make a video with the guided flow', async () => {
    await page.getByTestId('guided-artist-input').fill(SONG.artist);
    await page.getByTestId('guided-title-input').fill(SONG.title);
    await page.getByRole('button', { name: /choose audio/i }).click();

    // The fake search's lossless, well-seeded result is the "perfect match".
    await expect(page.getByTestId('pick-card')).toBeVisible();
    await page.getByRole('button', { name: /use this audio/i }).click();
    await page.getByText('Use audio as-is').click();
    await page.getByRole('button', { name: /keep private/i }).click();
    await expect(page.getByRole('heading', { name: 'Customize & Create' })).toBeVisible();
    await page.getByRole('button', { name: /create karaoke video/i }).click();
    await expect(page.getByText('Job Created')).toBeVisible();

    const idText = (await page.getByTestId('created-job-id').textContent()) || '';
    jobId = idText.match(/([a-f0-9]{8,})/i)?.[1] || '';
    expect(jobId, 'job id on the success card').toBeTruthy();
  });

  const token = await page.evaluate(() => window.localStorage.getItem('karaoke_access_token'));
  expect(token).toBeTruthy();

  await test.step('job reaches review and the card offers it', async () => {
    await waitForJobStatus(request, token!, jobId, 'awaiting_review');
    await page.getByRole('button', { name: /refresh/i }).first().click();
    await expect(page.getByRole('link', { name: /review lyrics/i }).first()).toBeVisible();
  });

  await test.step('review lyrics, preview, choose the instrumental', async () => {
    await page.goto(`/en/app/jobs#/${jobId}/review`);
    const preview = page.getByRole('button', { name: /preview video/i });
    await expect(preview).toBeVisible();
    // The fake lyrics are on the review screen.
    await expect(page.getByText('fake').first()).toBeVisible();
    await preview.click();
    const modal = page.getByRole('dialog');
    await expect(modal).toBeVisible();
    // The real local preview render runs against the canned audio.
    await expect(modal.locator('video')).toBeVisible({ timeout: 60_000 });

    const finish = page.getByRole('button', { name: /proceed to instrumental|complete track/i });
    await expect(finish).toBeEnabled();
    const label = (await finish.textContent()) || '';
    await finish.click();
    if (!/complete track/i.test(label)) {
      await expect(page).toHaveURL(new RegExp(`#/${jobId}/instrumental`));
      await page.locator('.selection-option:has-text("Clean")').first().click();
      await page.locator('#submit-btn').click();
    }
  });

  await test.step('video finishes and downloads work', async () => {
    await waitForJobStatus(request, token!, jobId, 'complete');
    await page.goto('/en/app');
    await page.getByRole('button', { name: /refresh/i }).first().click();
    const download = page.getByRole('link', { name: /720p/i }).first()
      .or(page.getByRole('button', { name: /720p/i }).first());
    await expect(download).toBeVisible();

    const res = await request.get(`/api/jobs/${jobId}/download/finals/lossy_720p_mp4`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status()).toBe(200);
    expect((await res.body()).length).toBeGreaterThan(10_000);

    const done = await waitForEmail(request, email, /ready/i);
    expect(done.subject).toContain(SONG.title);
  });
});
