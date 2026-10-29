// frontend/e2e/helpers/page-activity.ts
import type { Page, TestInfo } from '@playwright/test';
import * as fs from 'fs';

/**
 * Lightweight, public-safe stand-in for a Playwright trace on production E2E runs.
 *
 * Traces hold every request header (X-Admin-Token) and typed value, so CI keeps
 * them off and scrub-artifacts.mjs deletes them (karaoke-gen is a PUBLIC repo).
 * That left failures like "page stuck on the loading spinner" with nothing to go
 * on but a screenshot. This records only what's needed to see what the page was
 * waiting on — main-frame navigations, each request's method, origin+path (query
 * strings dropped: magic-link tokens, signed-URL signatures), status, duration or
 * failure, plus console errors/warnings and uncaught page errors — and on failure
 * writes it to `page-activity.log` in the test's output dir and prints the
 * requests still in flight.
 */

const MAX_ENTRIES = 2000;
const MAX_TEXT = 500;

/** origin + path only — query strings and fragments can carry tokens/signatures. */
export function sanitizeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    if (u.protocol === 'data:' || u.protocol === 'blob:') return `${u.protocol}…`;
    return `${u.origin}${u.pathname}`;
  } catch {
    return raw.split(/[?#]/)[0].slice(0, MAX_TEXT);
  }
}

function clip(text: string): string {
  const oneLine = text.replace(/\s+/g, ' ').trim();
  return oneLine.length > MAX_TEXT ? `${oneLine.slice(0, MAX_TEXT)}…` : oneLine;
}

export interface PageActivityRecorder {
  /** Activity lines so far (each prefixed with ms since recording started). */
  lines(): string[];
  /** Requests started but not finished/failed, as "METHOD url (Nms)". */
  pending(): string[];
  /** Writes page-activity.log + prints in-flight requests when the test failed. */
  writeOnFailure(testInfo: TestInfo): Promise<void>;
}

export function recordPageActivity(page: Page): PageActivityRecorder {
  const t0 = Date.now();
  const entries: string[] = [];
  const inFlight = new Map<object, { label: string; start: number }>();
  let dropped = 0;

  const log = (line: string) => {
    if (entries.length >= MAX_ENTRIES) {
      dropped++;
      return;
    }
    entries.push(`+${String(Date.now() - t0).padStart(6)}ms ${line}`);
  };

  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) log(`NAV      ${sanitizeUrl(frame.url())}`);
  });
  page.on('request', (req) => {
    inFlight.set(req, { label: `${req.method()} ${sanitizeUrl(req.url())}`, start: Date.now() });
  });
  page.on('requestfinished', async (req) => {
    const rec = inFlight.get(req);
    inFlight.delete(req);
    const status = (await req.response().catch(() => null))?.status() ?? '???';
    const label = rec?.label ?? `${req.method()} ${sanitizeUrl(req.url())}`;
    log(`REQ ${status} ${label} (${rec ? Date.now() - rec.start : '?'}ms)`);
  });
  page.on('requestfailed', (req) => {
    const rec = inFlight.get(req);
    inFlight.delete(req);
    const label = rec?.label ?? `${req.method()} ${sanitizeUrl(req.url())}`;
    log(`REQFAIL  ${label} ${req.failure()?.errorText ?? ''} (${rec ? Date.now() - rec.start : '?'}ms)`);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.type() === 'warning') log(`CONSOLE.${msg.type()} ${clip(msg.text())}`);
  });
  page.on('pageerror', (err) => log(`PAGEERROR ${clip(`${err.name}: ${err.message}`)}`));

  const pending = () => {
    const now = Date.now();
    return [...inFlight.values()].map((r) => `${r.label} (${now - r.start}ms)`);
  };
  const lines = () => (dropped ? [...entries, `… ${dropped} more entries dropped`] : [...entries]);

  return {
    lines,
    pending,
    async writeOnFailure(testInfo) {
      if (testInfo.status === testInfo.expectedStatus) return;
      const stillPending = pending();
      const body = [
        ...lines(),
        '',
        `IN FLIGHT AT FAILURE (${stillPending.length}):`,
        ...stillPending.map((p) => `  ${p}`),
      ].join('\n');
      const file = testInfo.outputPath('page-activity.log');
      fs.writeFileSync(file, `${body}\n`);
      await testInfo.attach('page-activity', { path: file, contentType: 'text/plain' });
      console.log(`[page-activity] ${stillPending.length} request(s) in flight at failure:`);
      for (const p of stillPending) console.log(`[page-activity]   ${p}`);
    },
  };
}
