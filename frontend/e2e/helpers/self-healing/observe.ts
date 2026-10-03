// frontend/e2e/helpers/self-healing/observe.ts
import type { Page } from '@playwright/test';
import { frameUrlKey } from './actions';
import type { Secrets } from './types';

/**
 * Builds the page observation the LLM fallback sees: per-frame ARIA snapshot +
 * a compact list of interactive elements (attributes only, NEVER input values),
 * plus a screenshot with every <input> masked.
 *
 * ⚠️ PCI: the real card number/expiry/CVC must never reach the LLM. Defence in
 * depth: values aren't collected, textbox values in ARIA snapshots are blanked,
 * PAN-like digit runs are scrubbed, every known secret is scrubbed verbatim,
 * and inputs are masked in the screenshot.
 */

const MAX_SNAPSHOT_CHARS_PER_FRAME = 8_000;
const MAX_ELEMENTS_PER_FRAME = 80;
const MAX_TOTAL_CHARS = 40_000;

export function redact(text: string, secrets: Secrets): string {
  let out = text
    // ARIA snapshot textbox/combobox/spinbutton lines carry the current value after ':'
    .replace(/^(\s*- (?:textbox|combobox|spinbutton|searchbox)\b[^\n:]*?(?:"[^"\n]*")?[^\n:]*):\s.*$/gm, '$1: [value redacted]')
    // Anything shaped like a card number (13-19 digits, optional space/dash separators)
    .replace(/\b(?:\d[ -]?){12,18}\d\b/g, '[redacted-digits]');
  for (const secret of Object.values(secrets)) {
    // Short secrets (CVC "123", ZIP) would scrub unrelated text everywhere; the
    // value-blanking above already covers them inside fields.
    if (secret && secret.replace(/\s/g, '').length >= 5) {
      out = out.split(secret).join('[redacted]');
    }
  }
  return out;
}

interface ElementInfo {
  tag: string;
  type?: string;
  role?: string;
  id?: string;
  name?: string;
  ariaLabel?: string;
  placeholder?: string;
  autocomplete?: string;
  testid?: string;
  text?: string;
  visible: boolean;
  checked?: boolean;
  disabled?: boolean;
}

export interface Observation {
  url: string;
  text: string;
  /** frame index (as referenced in `text`) → stable frame URL key. */
  frameKeys: string[];
  screenshotJpegBase64?: string;
}

export async function observe(page: Page, secrets: Secrets, withScreenshot = true): Promise<Observation> {
  const sections: string[] = [];
  const frames = page.frames().filter((f) => !f.isDetached());
  for (let i = 0; i < frames.length; i++) {
    const frame = frames[i];
    const key = frameUrlKey(frame.url());
    if (!key || key === 'about:blank') continue;

    const snapshot = await frame
      .locator('body')
      .ariaSnapshot({ timeout: 3_000 })
      .catch(() => '');
    const elements: ElementInfo[] = await frame
      .evaluate((max: number) => {
        const sel = 'input, button, select, textarea, a[href], label, [role], [data-testid]';
        const out: ElementInfo[] = [];
        for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
          if (out.length >= max) break;
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          const visible = r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
          const input = el as HTMLInputElement;
          const info: ElementInfo = { tag: el.tagName.toLowerCase(), visible };
          const attr = (n: string) => el.getAttribute(n) || undefined;
          info.type = attr('type');
          info.role = attr('role');
          info.id = el.id || undefined;
          info.name = attr('name');
          info.ariaLabel = attr('aria-label');
          info.placeholder = attr('placeholder');
          info.autocomplete = attr('autocomplete');
          info.testid = attr('data-testid');
          // Text content only for non-form elements — never read input values.
          if (!['input', 'select', 'textarea'].includes(info.tag)) {
            const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
            if (t) info.text = t.slice(0, 60);
          }
          if (info.type === 'checkbox' || info.type === 'radio') info.checked = input.checked;
          if ((el as HTMLButtonElement).disabled) info.disabled = true;
          out.push(info);
        }
        return out;
      }, MAX_ELEMENTS_PER_FRAME)
      .catch(() => []);

    if (!snapshot && elements.length === 0) continue;
    const elementLines = elements
      .map((e) => JSON.stringify(Object.fromEntries(Object.entries(e).filter(([, v]) => v !== undefined))))
      .join('\n');
    sections.push(
      `### frame[${i}] url=${key}\n` +
        `#### ARIA snapshot\n${snapshot.slice(0, MAX_SNAPSHOT_CHARS_PER_FRAME)}\n` +
        `#### Interactive elements (attributes only)\n${elementLines}`
    );
  }

  let text = redact(sections.join('\n\n'), secrets);
  if (text.length > MAX_TOTAL_CHARS) text = text.slice(0, MAX_TOTAL_CHARS) + '\n…[truncated]';

  let screenshotJpegBase64: string | undefined;
  if (withScreenshot) {
    const buf = await page
      .screenshot({
        type: 'jpeg',
        quality: 50,
        mask: frames.map((f) => f.locator('input, textarea')),
        maskColor: '#888888',
      })
      .catch(() => undefined);
    screenshotJpegBase64 = buf?.toString('base64');
  }

  return { url: frameUrlKey(page.url()), text, frameKeys: frames.map((f) => frameUrlKey(f.url())), screenshotJpegBase64 };
}
