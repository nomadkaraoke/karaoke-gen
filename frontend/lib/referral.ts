/**
 * Referral code storage — cookie + localStorage for resilience.
 */

const REFERRAL_COOKIE_NAME = 'nk_ref';
const REFERRAL_STORAGE_KEY = 'nk_referral_code';
const COOKIE_MAX_AGE_DAYS = 30;

export function setReferralCode(code: string): void {
  if (typeof window === 'undefined') return;
  const maxAge = COOKIE_MAX_AGE_DAYS * 24 * 60 * 60;
  document.cookie = `${REFERRAL_COOKIE_NAME}=${encodeURIComponent(code)};path=/;max-age=${maxAge};samesite=lax`;
  localStorage.setItem(REFERRAL_STORAGE_KEY, code);
}

export function getReferralCode(): string | null {
  if (typeof window === 'undefined') return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${REFERRAL_COOKIE_NAME}=([^;]*)`));
  if (match) return decodeURIComponent(match[1]);
  return localStorage.getItem(REFERRAL_STORAGE_KEY);
}

export function clearReferralCode(): void {
  if (typeof window === 'undefined') return;
  document.cookie = `${REFERRAL_COOKIE_NAME}=;path=/;max-age=0`;
  localStorage.removeItem(REFERRAL_STORAGE_KEY);
}
