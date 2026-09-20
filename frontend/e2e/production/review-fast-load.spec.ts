import { test, expect } from '@playwright/test';

/**
 * Review fast-full-load contract (2026-09-17 Option B re-architecture).
 *
 * Codifies the root-cause fix: the review page loads fast with audio available on first
 * paint, WITHOUT any per-load IAM signBlob round-trip. Verifies, against a real job in
 * (or re-openable to) review:
 *   1. GET /correction-data returns instrumental_options with non-null, same-origin
 *      proxy audio_urls (relative /api/review/{id}/instrumental-audio/{opt} paths) — never
 *      a signed storage.googleapis.com URL and never null.
 *   2. The proxy endpoint serves the transcoded OGG (200, audio/ogg, Accept-Ranges) and
 *      honors Range requests (206) so <audio> can seek.
 *   3. correction-data returns quickly (well under the old ~120s signBlob-stall ceiling).
 *
 * Env-gated (like lyrics-review-only.spec.ts) so it can run post-deploy against a known
 * job. Provide E2E_JOB_ID and one of E2E_TEST_TOKEN (admin/owner) or E2E_REVIEW_TOKEN.
 *
 * Usage:
 *   E2E_JOB_ID=xxx E2E_TEST_TOKEN=yyy npx playwright test review-fast-load.spec.ts \
 *     --config=playwright.production.config.ts
 */

const API_URL = 'https://api.nomadkaraoke.com';

const JOB_ID = process.env.E2E_JOB_ID;
const ACCESS_TOKEN = process.env.E2E_TEST_TOKEN || null;
const REVIEW_TOKEN = process.env.E2E_REVIEW_TOKEN || null;

// Auth for correction-data: Bearer for an access token, ?review_token= for a review link.
function correctionDataUrl(): string {
  const base = `${API_URL}/api/review/${JOB_ID}/correction-data`;
  return REVIEW_TOKEN ? `${base}?review_token=${encodeURIComponent(REVIEW_TOKEN)}` : base;
}
function authHeaders(): Record<string, string> {
  return ACCESS_TOKEN ? { Authorization: `Bearer ${ACCESS_TOKEN}` } : {};
}
// The proxy path is relative; auth rides in ?token= (access) or ?review_token= (link).
function proxyUrl(relativePath: string): string {
  const q = REVIEW_TOKEN
    ? `review_token=${encodeURIComponent(REVIEW_TOKEN)}`
    : ACCESS_TOKEN
      ? `token=${encodeURIComponent(ACCESS_TOKEN)}`
      : '';
  const sep = relativePath.includes('?') ? '&' : '?';
  return `${API_URL}${relativePath}${q ? sep + q : ''}`;
}

test.describe('Review fast-full-load contract', () => {
  test.describe.configure({ retries: 0 });
  test.skip(!JOB_ID || (!ACCESS_TOKEN && !REVIEW_TOKEN),
    'Set E2E_JOB_ID and E2E_TEST_TOKEN or E2E_REVIEW_TOKEN to run');

  test('correction-data returns proxy audio URLs quickly, with no signing', async ({ request }) => {
    const started = Date.now();
    const res = await request.get(correctionDataUrl(), { headers: authHeaders() });
    const elapsedMs = Date.now() - started;
    console.log(`correction-data status=${res.status()} elapsed=${elapsedMs}ms`);

    expect(res.ok()).toBeTruthy();
    // Fast: comfortably under the old ~120s signBlob-stall ceiling (and typical healthy load).
    expect(elapsedMs).toBeLessThan(15_000);

    const data = await res.json();
    const options = data.instrumental_options ?? [];
    expect(options.length).toBeGreaterThan(0);

    for (const opt of options) {
      // Non-null, and a same-origin proxy PATH — never a signed GCS URL.
      expect(opt.audio_url, `option ${opt.id} audio_url`).toBeTruthy();
      expect(opt.audio_url).toContain(`/api/review/${JOB_ID}/instrumental-audio/`);
      expect(opt.audio_url).not.toContain('storage.googleapis.com');
      expect(opt.audio_url).not.toContain('X-Goog-Signature');
    }

    // Dead field must not be emitted (waveform comes from the JSON /waveform-data endpoint).
    expect(data.backing_vocals_waveform_url).toBeUndefined();

    // The proxy actually serves playable audio, with Range support for seeking.
    const first = options[0];
    const audioRes = await request.get(proxyUrl(first.audio_url));
    console.log(`instrumental-audio status=${audioRes.status()} type=${audioRes.headers()['content-type']}`);
    expect(audioRes.ok()).toBeTruthy();
    expect(audioRes.headers()['content-type']).toContain('audio/ogg');
    expect(audioRes.headers()['accept-ranges']).toBe('bytes');

    const ranged = await request.get(proxyUrl(first.audio_url), { headers: { Range: 'bytes=0-1023' } });
    expect(ranged.status()).toBe(206);
    expect(ranged.headers()['content-range']).toMatch(/^bytes 0-\d+\/\d+$/);
  });

  // Concurrent-load hardening (v0.230.0): vocals byte proxy + cached waveform-data.
  test('vocals audio serves with Range support and cache headers', async ({ request }) => {
    const url = proxyUrl(`/api/review/${JOB_ID}/audio/vocals`);

    const full = await request.get(url);
    console.log(`audio/vocals status=${full.status()} type=${full.headers()['content-type']}`);
    expect(full.ok()).toBeTruthy();
    expect(full.headers()['content-type']).toMatch(/audio\//);
    expect(full.headers()['accept-ranges']).toBe('bytes');
    expect(full.headers()['cache-control']).toContain('max-age');

    const ranged = await request.get(url, { headers: { Range: 'bytes=0-1023' } });
    expect(ranged.status()).toBe(206);
    expect(ranged.headers()['content-range']).toMatch(/^bytes 0-\d+\/\d+$/);
    expect((await ranged.body()).length).toBe(1024);
  });

  test('waveform-data returns quickly from the persistent cache', async ({ request }) => {
    const base = `${API_URL}/api/review/${JOB_ID}/waveform-data`;
    const url = REVIEW_TOKEN ? `${base}?review_token=${encodeURIComponent(REVIEW_TOKEN)}` : base;

    // First call may compute+cache (bounded, off the event loop); the second
    // must be a pure GCS-cache read and come back fast.
    const warm = await request.get(url, { headers: authHeaders() });
    expect(warm.ok()).toBeTruthy();

    const started = Date.now();
    const res = await request.get(url, { headers: authHeaders() });
    const elapsedMs = Date.now() - started;
    console.log(`waveform-data (cached) status=${res.status()} elapsed=${elapsedMs}ms`);
    expect(res.ok()).toBeTruthy();
    expect(elapsedMs).toBeLessThan(10_000);

    const data = await res.json();
    expect(Array.isArray(data.amplitudes)).toBeTruthy();
    expect(data.amplitudes.length).toBeGreaterThan(0);
    expect(data.duration_seconds).toBeGreaterThan(0);
  });

  // Sub-second strips (v0.232.0): precomputed vocals peak envelope.
  test('vocals-peaks returns a compact cached envelope quickly', async ({ request }) => {
    const base = `${API_URL}/api/review/${JOB_ID}/vocals-peaks`;
    const url = REVIEW_TOKEN ? `${base}?review_token=${encodeURIComponent(REVIEW_TOKEN)}` : base;

    // First call may compute+cache; the second must be a pure cache read.
    const warm = await request.get(url, { headers: authHeaders() });
    expect([200, 202]).toContain(warm.status());
    if (warm.status() === 202) return; // separation still running for this job

    const started = Date.now();
    const res = await request.get(url, { headers: authHeaders() });
    const elapsedMs = Date.now() - started;
    console.log(`vocals-peaks (cached) status=${res.status()} elapsed=${elapsedMs}ms`);
    expect(res.ok()).toBeTruthy();
    expect(elapsedMs).toBeLessThan(5_000);
    expect(res.headers()['cache-control']).toContain('max-age');

    const data = await res.json();
    expect(data.encoding).toBe('u8');
    expect(typeof data.peaks_b64).toBe('string');
    expect(data.peaks_b64.length).toBeGreaterThan(0);
    expect(data.peaks_per_second).toBe(400);
    expect(data.duration_seconds).toBeGreaterThan(0);
    // Envelope should be dramatically smaller than the audio it replaces.
    expect(data.peaks_b64.length).toBeLessThan(1_000_000);
  });
});
