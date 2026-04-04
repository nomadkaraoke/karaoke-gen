'use client';

import { useLocale, useTranslations } from 'next-intl';
import { usePathname, useRouter, routing } from '@/i18n/routing';

const localeNames: Record<string, string> = {
  en: 'English',
  es: 'Español',
  de: 'Deutsch',
};

export default function LanguageSwitcher() {
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations('languageSwitcher');

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const newLocale = e.target.value;
    try { localStorage.setItem('locale', newLocale); } catch {}
    router.replace(pathname, { locale: newLocale });
  }

  return (
    <select
      value={locale}
      onChange={handleChange}
      aria-label={t('label')}
      className="bg-transparent border border-border/50 rounded-md px-2 py-1 text-sm cursor-pointer text-muted-foreground hover:text-foreground transition-colors"
    >
      {routing.locales.map((loc) => (
        <option key={loc} value={loc}>{localeNames[loc]}</option>
      ))}
    </select>
  );
}
