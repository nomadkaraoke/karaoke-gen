// frontend/e2e/helpers/self-healing/actions.ts
import type { Frame, Locator, Page } from '@playwright/test';
import type { Action, Secrets, TargetSpec } from './types';

const PLACEHOLDER_RE = /\{\{([A-Z_]+)\}\}/g;

/** Substitute `{{NAME}}` placeholders. Throws on unknown placeholders so a typo can't type literal braces into a payment form. */
export function substitutePlaceholders(value: string, secrets: Secrets): string {
  return value.replace(PLACEHOLDER_RE, (_, key: string) => {
    if (!(key in secrets)) throw new Error(`Unknown placeholder {{${key}}}`);
    return secrets[key];
  });
}

/** Path segments that identify a session/object rather than a page (e.g. `cs_live_a1j04…`). */
function isDynamicSegment(seg: string): boolean {
  // Stripe object ids, or long opaque tokens (no '.'/'-' like real page names).
  return (
    /^[a-z]{2,5}_(live|test)_/i.test(seg) ||
    (seg.length >= 16 && /^[A-Za-z0-9_]+$/.test(seg) && /\d/.test(seg) && /[a-z]/i.test(seg))
  );
}

/**
 * Stable frame key: host + path up to the first session-specific segment, no
 * query/hash. Stripe puts the checkout session id in the PATH
 * (`checkout.stripe.com/c/pay/cs_live_…`), so stripping the query isn't enough —
 * a learned recipe must never pin (or leak) one run's session id.
 */
export function frameUrlKey(url: string): string {
  try {
    const u = new URL(url);
    const kept: string[] = [];
    for (const seg of u.pathname.split('/').filter(Boolean)) {
      if (isDynamicSegment(seg)) break;
      kept.push(seg);
    }
    return kept.length ? `${u.host}/${kept.join('/')}` : u.host;
  } catch {
    return url;
  }
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function nameMatcher(name: string, exact?: boolean): string | RegExp {
  return exact ? name : new RegExp(escapeRegex(name), 'i');
}

/** Build a frame-scoped locator for a TargetSpec. */
export function targetLocator(frame: Frame, t: TargetSpec): Locator {
  switch (t.by) {
    case 'role':
      if (!t.role) throw new Error('TargetSpec by=role requires role');
      return frame.getByRole(t.role as Parameters<Frame['getByRole']>[0], {
        ...(t.name ? { name: nameMatcher(t.name, t.exact), exact: t.exact } : {}),
        includeHidden: t.includeHidden,
      });
    case 'text':
      if (!t.text) throw new Error('TargetSpec by=text requires text');
      return frame.getByText(nameMatcher(t.text, t.exact));
    case 'placeholder':
      if (!t.text) throw new Error('TargetSpec by=placeholder requires text');
      return frame.getByPlaceholder(nameMatcher(t.text, t.exact));
    case 'label':
      if (!t.text) throw new Error('TargetSpec by=label requires text');
      return frame.getByLabel(nameMatcher(t.text, t.exact));
    case 'css':
      if (!t.css) throw new Error('TargetSpec by=css requires css');
      return frame.locator(t.css);
    default:
      throw new Error(`Unknown TargetSpec.by: ${(t as TargetSpec).by}`);
  }
}

function orderedFrames(page: Page, t: TargetSpec): Frame[] {
  const frames = page.frames().filter((f) => !f.isDetached());
  if (!t.frameUrlIncludes) return frames;
  const preferred = frames.filter((f) => frameUrlKey(f.url()).includes(t.frameUrlIncludes!));
  return [...preferred, ...frames.filter((f) => !preferred.includes(f))];
}

/**
 * Find the element for a TargetSpec in any frame (preferred frame first),
 * polling until `timeoutMs`. Visible matches win; with `includeHidden`, an
 * attached-but-hidden match is accepted as a last resort.
 */
export async function resolveTarget(
  page: Page,
  t: TargetSpec,
  timeoutMs = 5_000
): Promise<{ locator: Locator; visible: boolean } | null> {
  const deadline = Date.now() + timeoutMs;
  do {
    let hidden: Locator | null = null;
    for (const frame of orderedFrames(page, t)) {
      let loc: Locator;
      try {
        loc = targetLocator(frame, t).first();
      } catch {
        return null; // malformed spec — retrying won't help
      }
      if (await loc.isVisible().catch(() => false)) return { locator: loc, visible: true };
      if (t.includeHidden && !hidden && (await loc.count().catch(() => 0)) > 0) hidden = loc;
    }
    if (hidden) return { locator: hidden, visible: false };
    await page.waitForTimeout(250);
  } while (Date.now() < deadline);
  return null;
}

/** Execute one Action. Throws if the target can't be found or the action fails. */
export async function executeAction(
  page: Page,
  action: Action,
  secrets: Secrets,
  timeoutMs = 5_000
): Promise<Locator> {
  const found = await resolveTarget(page, action.target, timeoutMs);
  if (!found) throw new Error(`Target not found: ${JSON.stringify(action.target)}`);
  const { locator, visible } = found;
  const value = action.value !== undefined ? substitutePlaceholders(action.value, secrets) : undefined;

  switch (action.type) {
    case 'click':
      if (visible) await locator.click({ force: true, timeout: timeoutMs });
      else await locator.dispatchEvent('click');
      break;
    case 'fill':
      if (value === undefined) throw new Error('fill requires value');
      await locator.click({ timeout: timeoutMs });
      await locator.fill('', { timeout: timeoutMs }).catch(() => {});
      // Stripe's card inputs reformat as you type; typing (not fill) keeps their
      // input handlers and validation in sync.
      await locator.pressSequentially(value, { delay: 40, timeout: timeoutMs * 4 });
      break;
    case 'select':
      if (value === undefined) throw new Error('select requires value');
      await locator.selectOption({ label: value }, { timeout: timeoutMs }).catch(() =>
        locator.selectOption(value, { timeout: timeoutMs })
      );
      break;
    case 'check':
      await locator.check({ force: true, timeout: timeoutMs });
      break;
    case 'uncheck':
      await locator.uncheck({ force: true, timeout: timeoutMs });
      break;
    case 'press':
      await locator.press(value || 'Enter', { timeout: timeoutMs });
      break;
    default:
      throw new Error(`Unknown action type: ${(action as Action).type}`);
  }
  return locator;
}
