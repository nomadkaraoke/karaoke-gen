'use client';

import { useEffect } from 'react';
import { routing } from '@/i18n/routing';

/**
 * Detects the user's preferred locale and redirects to the locale-prefixed
 * version of the current URL. Preserves path, query string, and hash.
 *
 * Used by legacy non-locale routes (e.g., /app → /en/app) to redirect
 * to the proper [locale] routes.
 */
export function LocaleRedirect() {
  useEffect(() => {
    const locale = detectLocale();
    const { pathname, search, hash } = window.location;
    window.location.replace(`/${locale}${pathname}${search}${hash}`);
  }, []);

  return null;
}

function detectLocale(): string {
  // 1. Check localStorage for saved preference
  try {
    const saved = localStorage.getItem('locale');
    if (saved && routing.locales.includes(saved as typeof routing.locales[number])) {
      return saved;
    }
  } catch {
    // localStorage not available
  }

  // 2. Detect from browser language
  const browserLangs = navigator.languages || [navigator.language];
  for (const lang of browserLangs) {
    const prefix = lang.split('-')[0].toLowerCase();
    if (routing.locales.includes(prefix as typeof routing.locales[number])) {
      return prefix;
    }
  }

  // 3. Fallback to default
  return routing.defaultLocale;
}
