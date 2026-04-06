'use client';

import { useState, useRef, useEffect } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { usePathname, useRouter, routing } from '@/i18n/routing';

const LOCALE_INFO: { code: string; native: string; english: string; flag: string }[] = [
  { code: 'en', native: 'English', english: 'English', flag: '\u{1F1EC}\u{1F1E7}' },
  { code: 'es', native: 'Espa\u00f1ol', english: 'Spanish', flag: '\u{1F1EA}\u{1F1F8}' },
  { code: 'de', native: 'Deutsch', english: 'German', flag: '\u{1F1E9}\u{1F1EA}' },
  { code: 'pt', native: 'Portugu\u00eas', english: 'Portuguese', flag: '\u{1F1E7}\u{1F1F7}' },
  { code: 'fr', native: 'Fran\u00e7ais', english: 'French', flag: '\u{1F1EB}\u{1F1F7}' },
  { code: 'ja', native: '\u65e5\u672c\u8a9e', english: 'Japanese', flag: '\u{1F1EF}\u{1F1F5}' },
  { code: 'ko', native: '\ud55c\uad6d\uc5b4', english: 'Korean', flag: '\u{1F1F0}\u{1F1F7}' },
  { code: 'zh', native: '\u4e2d\u6587', english: 'Chinese', flag: '\u{1F1E8}\u{1F1F3}' },
  { code: 'it', native: 'Italiano', english: 'Italian', flag: '\u{1F1EE}\u{1F1F9}' },
  { code: 'nl', native: 'Nederlands', english: 'Dutch', flag: '\u{1F1F3}\u{1F1F1}' },
  { code: 'pl', native: 'Polski', english: 'Polish', flag: '\u{1F1F5}\u{1F1F1}' },
  { code: 'tr', native: 'T\u00fcrk\u00e7e', english: 'Turkish', flag: '\u{1F1F9}\u{1F1F7}' },
  { code: 'ru', native: '\u0420\u0443\u0441\u0441\u043a\u0438\u0439', english: 'Russian', flag: '\u{1F1F7}\u{1F1FA}' },
  { code: 'th', native: '\u0e44\u0e17\u0e22', english: 'Thai', flag: '\u{1F1F9}\u{1F1ED}' },
  { code: 'id', native: 'Indonesia', english: 'Indonesian', flag: '\u{1F1EE}\u{1F1E9}' },
  { code: 'vi', native: 'Ti\u1ebfng Vi\u1ec7t', english: 'Vietnamese', flag: '\u{1F1FB}\u{1F1F3}' },
  { code: 'tl', native: 'Filipino', english: 'Filipino', flag: '\u{1F1F5}\u{1F1ED}' },
  { code: 'hi', native: '\u0939\u093f\u0928\u094d\u0926\u0940', english: 'Hindi', flag: '\u{1F1EE}\u{1F1F3}' },
  { code: 'ar', native: '\u0627\u0644\u0639\u0631\u0628\u064a\u0629', english: 'Arabic', flag: '\u{1F1F8}\u{1F1E6}' },
  { code: 'sv', native: 'Svenska', english: 'Swedish', flag: '\u{1F1F8}\u{1F1EA}' },
  { code: 'nb', native: 'Norsk', english: 'Norwegian', flag: '\u{1F1F3}\u{1F1F4}' },
  { code: 'da', native: 'Dansk', english: 'Danish', flag: '\u{1F1E9}\u{1F1F0}' },
  { code: 'fi', native: 'Suomi', english: 'Finnish', flag: '\u{1F1EB}\u{1F1EE}' },
  { code: 'cs', native: '\u010ce\u0161tina', english: 'Czech', flag: '\u{1F1E8}\u{1F1FF}' },
  { code: 'ro', native: 'Rom\u00e2n\u0103', english: 'Romanian', flag: '\u{1F1F7}\u{1F1F4}' },
  { code: 'hu', native: 'Magyar', english: 'Hungarian', flag: '\u{1F1ED}\u{1F1FA}' },
  { code: 'el', native: '\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac', english: 'Greek', flag: '\u{1F1EC}\u{1F1F7}' },
  { code: 'he', native: '\u05e2\u05d1\u05e8\u05d9\u05ea', english: 'Hebrew', flag: '\u{1F1EE}\u{1F1F1}' },
  { code: 'ms', native: 'Melayu', english: 'Malay', flag: '\u{1F1F2}\u{1F1FE}' },
  { code: 'uk', native: '\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430', english: 'Ukrainian', flag: '\u{1F1FA}\u{1F1E6}' },
  { code: 'hr', native: 'Hrvatski', english: 'Croatian', flag: '\u{1F1ED}\u{1F1F7}' },
  { code: 'sk', native: 'Sloven\u010dina', english: 'Slovak', flag: '\u{1F1F8}\u{1F1F0}' },
  { code: 'ca', native: 'Catal\u00e0', english: 'Catalan', flag: '\u{1F3F4}' },
];

export default function LanguageSwitcher({ showLabel }: { showLabel?: boolean } = {}) {
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations('languageSwitcher');
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const current = LOCALE_INFO.find((l) => l.code === locale) || LOCALE_INFO[0];

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [open]);

  function handleSelect(code: string) {
    setOpen(false);
    try { localStorage.setItem('locale', code); } catch {}
    router.replace(pathname, { locale: code });
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={t('label')}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 bg-transparent border border-border/50 rounded-md px-2 py-1 text-sm cursor-pointer text-muted-foreground hover:text-foreground hover:border-border transition-colors"
      >
        <span className="text-base leading-none">{current.flag}</span>
        <span className={showLabel ? '' : 'hidden sm:inline'}>{current.native}</span>
      </button>

      {open && (
        <div className="absolute end-0 top-full mt-1 z-50 w-64 max-h-80 overflow-y-auto rounded-md border border-border bg-popover shadow-lg">
          {LOCALE_INFO.filter((l) =>
            (routing.locales as readonly string[]).includes(l.code)
          ).map((l) => (
            <button
              key={l.code}
              onClick={() => handleSelect(l.code)}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors hover:bg-accent hover:text-accent-foreground ${
                l.code === locale
                  ? 'bg-accent/50 text-accent-foreground font-medium'
                  : 'text-popover-foreground'
              }`}
            >
              <span className="text-base leading-none shrink-0">{l.flag}</span>
              <span className="truncate">{l.native}</span>
              {l.native !== l.english && (
                <span className="text-muted-foreground text-xs truncate ms-auto">{l.english}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
