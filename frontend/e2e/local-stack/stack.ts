/**
 * Client for the local stack's test-only API (/api/test/*, backend/api/routes/test_support.py).
 */
import { APIRequestContext, Page, expect } from '@playwright/test';

export const API_URL = process.env.LOCAL_STACK_API_URL || 'http://127.0.0.1:8010';

export interface SeededUser {
  email: string;
  session_token: string;
  credits: number;
  job_id?: string;
}

export interface FakeConfig {
  step_delay_seconds?: number;
  auto_approve?: boolean;
  fail_at?: 'audio_download' | 'audio' | 'lyrics' | 'screens' | 'render_video' | 'video' | null;
  audio_duration_seconds?: number;
  search_result_count?: number;
}

export interface CapturedEmail {
  to: string;
  subject: string;
  links: string[];
  codes: string[];
  created_at: string;
}

export function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}@example.com`;
}

async function ok<T>(response: Awaited<ReturnType<APIRequestContext['get']>>): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${response.url()} -> ${response.status()} ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export async function scenario(
  request: APIRequestContext,
  name: string,
  opts: { email?: string; credits?: number; artist?: string; title?: string } = {},
): Promise<SeededUser> {
  return ok(await request.post(`${API_URL}/api/test/scenario`, { data: { name, ...opts }, timeout: 150_000 }));
}

/** Per-user fake-worker overrides (they only affect jobs owned by `email`). */
export async function setFakeConfig(request: APIRequestContext, email: string, config: FakeConfig): Promise<void> {
  await ok(await request.put(`${API_URL}/api/test/fake-config?email=${encodeURIComponent(email)}`, { data: config }));
}

export async function emailsTo(request: APIRequestContext, to: string): Promise<CapturedEmail[]> {
  const body = await ok<{ emails: CapturedEmail[] }>(
    await request.get(`${API_URL}/api/test/emails?to=${encodeURIComponent(to)}`),
  );
  return body.emails;
}

/** Wait for the newest email to `to` whose subject matches, and return it. */
export async function waitForEmail(
  request: APIRequestContext,
  to: string,
  subject: RegExp,
  timeoutMs = 30_000,
): Promise<CapturedEmail> {
  let found: CapturedEmail | undefined;
  await expect
    .poll(async () => {
      found = (await emailsTo(request, to)).find((e) => subject.test(e.subject));
      return !!found;
    }, { timeout: timeoutMs, message: `email to ${to} matching ${subject}` })
    .toBe(true);
  return found!;
}

export async function jobStatus(request: APIRequestContext, token: string, jobId: string): Promise<string> {
  const job = await ok<{ status: string }>(
    await request.get(`${API_URL}/api/jobs/${jobId}`, { headers: { Authorization: `Bearer ${token}` } }),
  );
  return job.status;
}

export async function waitForJobStatus(
  request: APIRequestContext,
  token: string,
  jobId: string,
  status: string,
  timeoutMs = 60_000,
): Promise<void> {
  await expect
    .poll(() => jobStatus(request, token, jobId), { timeout: timeoutMs, message: `job ${jobId} -> ${status}` })
    .toBe(status);
}

/** Sign the browser in as a seeded user (token in localStorage before any page script runs). */
export async function signInAs(page: Page, user: SeededUser): Promise<void> {
  await page.addInitScript((token) => {
    window.localStorage.setItem('karaoke_access_token', token);
  }, user.session_token);
}
