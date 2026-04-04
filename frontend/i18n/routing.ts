import { defineRouting } from 'next-intl/routing';
import { createNavigation } from 'next-intl/navigation';

export const routing = defineRouting({
  locales: [
    'en', 'es', 'de', 'pt', 'fr', 'ja', 'ko', 'zh', 'it', 'nl',
    'pl', 'tr', 'ru', 'th', 'id', 'vi', 'tl', 'hi', 'ar', 'sv',
    'nb', 'da', 'fi', 'cs', 'ro', 'hu', 'el', 'he', 'ms', 'uk',
    'hr', 'sk', 'ca',
  ],
  defaultLocale: 'en'
});

export const { Link, redirect, usePathname, useRouter } =
  createNavigation(routing);
