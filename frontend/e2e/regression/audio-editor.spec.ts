import { test, expect, Page } from '@playwright/test';
import { setupApiFixtures, setAuthToken } from '../fixtures/test-helper';

/**
 * Audio Editor — Tempo change + mid-track fades
 *
 * The audio editor can speed up / slow down the whole track (pitch preserved).
 * Tempo-changed tracks are labeled "(NN% Tempo)" in every published output, so
 * the editor must make that clear before the user submits.
 */

const TEST_JOB_ID = 'test-audio-edit-tempo';

const mockJobData = {
  job_id: TEST_JOB_ID,
  status: 'awaiting_audio_edit',
  progress: 15,
  created_at: '2026-09-26T00:00:00Z',
  updated_at: '2026-09-26T00:00:00Z',
  artist: 'Queen',
  title: 'Bohemian Rhapsody',
  user_email: 'test@example.com',
};

const amplitudes = Array(400).fill(0).map((_, i) => Math.abs(Math.sin(i * 0.07)) * 0.8 + 0.1);

const mockAudioInfo = {
  job_id: TEST_JOB_ID,
  artist: 'Queen',
  title: 'Bohemian Rhapsody',
  original_duration_seconds: 200,
  current_duration_seconds: 200,
  original_audio_url: 'https://storage.example.com/original.ogg',
  current_audio_url: 'https://storage.example.com/current.ogg',
  waveform_data: { amplitudes },
  original_waveform_data: { amplitudes },
  edit_stack: [],
  can_undo: false,
  can_redo: false,
};

const tempoEntry = {
  edit_id: 'tempo-1',
  operation: 'tempo',
  params: { factor: 0.9 },
  duration_before: 200,
  duration_after: 222.2,
  timestamp: '2026-09-26T00:01:00Z',
};

const mockApplyResponse = {
  status: 'success',
  edit_id: 'tempo-1',
  operation: 'tempo',
  duration_before: 200,
  duration_after: 222.2,
  current_audio_url: 'https://storage.example.com/edited.ogg',
  waveform_data: { amplitudes },
  edit_stack: [tempoEntry],
  can_undo: true,
  can_redo: false,
};

async function setupMocks(page: Page) {
  await setupApiFixtures(page, {
    mocks: [
      { method: 'GET', path: `/api/jobs/${TEST_JOB_ID}`, response: { body: mockJobData } },
      { method: 'GET', path: `/api/review/${TEST_JOB_ID}/input-audio-info`, response: { body: mockAudioInfo } },
      { method: 'GET', path: `/api/review/${TEST_JOB_ID}/audio-edit-sessions`, response: { body: { sessions: [] } } },
      { method: 'POST', path: `/api/review/${TEST_JOB_ID}/audio-edit-sessions`, response: { body: { status: 'saved' } } },
      { method: 'POST', path: `/api/review/${TEST_JOB_ID}/audio-edit/apply`, response: { body: mockApplyResponse } },
      {
        method: 'GET',
        path: '/api/users/me',
        response: { body: { user: { email: 'test@example.com', role: 'user', credits: 10 }, has_session: true } },
      },
      { method: 'GET', path: '/api/tenant/config', response: { body: { tenant: null, is_default: true } } },
    ],
  });
  await page.route('**/storage.example.com/**', (route) =>
    route.fulfill({ status: 200, body: Buffer.alloc(0), contentType: 'audio/ogg' }),
  );
}

test.describe('Audio Editor - Tempo', () => {
  test('changes tempo and warns that outputs will be labeled', async ({ page }) => {
    await setAuthToken(page, 'test-token-123');
    await setupMocks(page);

    const applyRequest = page.waitForRequest(
      (req) => req.url().includes('/audio-edit/apply') && req.method() === 'POST',
    );

    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/audio-edit`);
    await expect(page.getByText('Audio Editor')).toBeVisible({ timeout: 15000 });

    await page.getByTestId('tempo-button').click();
    const dialog = page.getByTestId('tempo-dialog');
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId('tempo-apply')).toBeDisabled();

    await dialog.getByRole('button', { name: '90%', exact: true }).click();
    await expect(dialog).toContainText('Length: 3:20 → 3:42');
    await expect(dialog).toContainText('(90% Tempo)');
    await page.screenshot({ path: 'test-results/audio-editor-tempo-dialog.png', animations: 'disabled' });

    await page.getByTestId('tempo-apply').click();
    const req = await applyRequest;
    expect(req.postDataJSON()).toEqual({ operation: 'tempo', params: { factor: 0.9 } });

    await expect(page.getByTestId('tempo-badge')).toHaveText('Tempo: 90% of original');
    await expect(page.getByTestId('tempo-button')).toContainText('90%');

    await page.getByRole('button', { name: 'Submit for Review' }).click();
    await expect(page.getByTestId('tempo-submit-warning')).toContainText('"(90% Tempo)"');
    await page.screenshot({ path: 'test-results/audio-editor-tempo-submit.png', animations: 'disabled' });
  });
});

test.describe('Audio Editor - Mid-track fades', () => {
  test('fades can be applied to a selection in the middle of the track', async ({ page }) => {
    await setAuthToken(page, 'test-token-123');
    await setupMocks(page);

    await page.goto(`/app/jobs/#/${TEST_JOB_ID}/audio-edit`);
    await expect(page.getByText('Audio Editor')).toBeVisible({ timeout: 15000 });

    // Select ~50s-100s of the 200s track by dragging across the waveform
    const canvas = page.locator('canvas').first();
    const box = (await canvas.boundingBox())!;
    const y = box.y + box.height / 3;
    await page.mouse.move(box.x + box.width * 0.25, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.5, y, { steps: 5 });
    await page.mouse.up();

    await expect(page.getByRole('button', { name: 'Trim Start' })).toBeDisabled();
    const fadeOut = page.getByRole('button', { name: 'Fade Out' });
    await expect(fadeOut).toBeEnabled();

    const applyRequest = page.waitForRequest(
      (req) => req.url().includes('/audio-edit/apply') && req.method() === 'POST',
    );
    await fadeOut.click();
    const body = (await applyRequest).postDataJSON();
    expect(body.operation).toBe('fade_out');
    expect(body.params.start_seconds).toBeGreaterThan(40);
    expect(body.params.end_seconds).toBeLessThan(110);
  });
});
