'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { routing } from '@/i18n/routing';

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    // 1. Check localStorage for saved preference
    try {
      const saved = localStorage.getItem('locale');
      if (saved && routing.locales.includes(saved as typeof routing.locales[number])) {
        router.replace(`/${saved}/`);
        return;
      }
    } catch {
      // localStorage not available
    }

    // 2. Detect from browser language
    const browserLangs = navigator.languages || [navigator.language];
    for (const lang of browserLangs) {
      const prefix = lang.split('-')[0].toLowerCase();
      if (routing.locales.includes(prefix as typeof routing.locales[number])) {
        router.replace(`/${prefix}/`);
        return;
      }
    }

    // 3. Fallback to default
    router.replace(`/${routing.defaultLocale}/`);
  }, [router]);

  return null;
}
