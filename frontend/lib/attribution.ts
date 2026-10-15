/**
 * First-touch marketing attribution (UTM tags + external referrer).
 *
 * The first landing that carries utm_* params or arrives from another site is
 * stored for 30 days and sent with the magic-link request, where the backend
 * records it on the new account (signup_source / signup_attribution). Later
 * landings never overwrite it: we want the channel that brought the person.
 */

const ATTRIBUTION_STORAGE_KEY = 'nk_attribution';
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'] as const;

export type Attribution = Partial<Record<(typeof UTM_KEYS)[number], string>> & {
  referrer?: string;
  landing_path?: string;
  captured_at: string;
};

// Only gen itself is in-app navigation; nomadkaraoke.com / decide / kjbox referrers
// are real channels and the backend labels them.
function isGenHost(host: string): boolean {
  return host === window.location.hostname || host === 'gen.nomadkaraoke.com';
}

/** Build an attribution record for the current landing, or null if it has no marketing signal. */
export function attributionFromLanding(search: string, referrer: string, pathname: string): Attribution | null {
  const params = new URLSearchParams(search);
  const record: Attribution = { captured_at: new Date().toISOString() };
  for (const key of UTM_KEYS) {
    const value = params.get(key)?.trim();
    if (value) record[key] = value.slice(0, 200);
  }
  if (referrer) {
    try {
      const host = new URL(referrer).hostname.toLowerCase();
      if (host && !isGenHost(host)) record.referrer = host;
    } catch {
      // Malformed referrer: ignore
    }
  }
  if (!record.utm_source && !record.utm_medium && !record.utm_campaign && !record.referrer) return null;
  record.landing_path = pathname.slice(0, 200);
  return record;
}

export function getAttribution(): Attribution | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(ATTRIBUTION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Attribution;
    const capturedAt = Date.parse(parsed?.captured_at ?? '')
    if (!Number.isFinite(capturedAt) || Date.now() - capturedAt > MAX_AGE_MS) {
      localStorage.removeItem(ATTRIBUTION_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/** Store this landing's attribution unless an unexpired first touch already exists. */
export function captureAttribution(): void {
  if (typeof window === 'undefined') return;
  if (getAttribution()) return;
  const record = attributionFromLanding(window.location.search, document.referrer, window.location.pathname);
  if (!record) return;
  try {
    localStorage.setItem(ATTRIBUTION_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Storage unavailable (private mode): attribution is best-effort
  }
}
