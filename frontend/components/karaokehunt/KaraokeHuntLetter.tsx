'use client';

import { useTranslations } from 'next-intl';
import { Music } from 'lucide-react';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { ThemeToggle } from '@/components/ThemeToggle';

// Paragraph keys rendered in order; 'credits' renders creditsBefore + link + creditsAfter.
export type LetterParagraph = string;

interface KaraokeHuntLetterProps {
  namespace: 'karaokehuntLetter' | 'karaokehuntListLetter';
  paragraphs: LetterParagraph[];
}

// Shared layout for the letters to former KaraokeHunt users, linked from outreach emails.
export default function KaraokeHuntLetter({ namespace, paragraphs }: KaraokeHuntLetterProps) {
  const t = useTranslations(namespace);

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)' }}>
      <header className="flex items-center justify-between px-4 py-3 max-w-2xl mx-auto">
        <div className="flex items-center gap-2" style={{ color: 'var(--text)' }}>
          <Music className="h-5 w-5" style={{ color: 'var(--accent)' }} />
          <span className="font-semibold">Nomad Karaoke</span>
        </div>
        <div className="flex items-center gap-2">
          <LanguageSwitcher />
          <ThemeToggle />
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 pb-16">
        <div
          className="rounded-2xl p-6 sm:p-10 mt-4"
          style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
        >
          <h1 className="text-2xl sm:text-3xl font-bold mb-6">{t('title')}</h1>

          <div className="space-y-4 leading-relaxed">
            <p>{t('greeting')}</p>
            {paragraphs.map((key) =>
              key === 'credits' ? (
                <p key={key}>
                  {t('creditsBefore')}{' '}
                  <a
                    href="https://gen.nomadkaraoke.com"
                    className="underline font-medium"
                    style={{ color: 'var(--accent)' }}
                  >
                    gen.nomadkaraoke.com
                  </a>{' '}
                  {t('creditsAfter')}
                </p>
              ) : (
                <p key={key}>{t(key)}</p>
              )
            )}
            <p>
              {t('signoff')}
              <br />
              Andrew
              <br />
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                {t('founderRole')}
              </span>
            </p>
          </div>
        </div>

        <p className="text-center text-sm mt-6" style={{ color: 'var(--text-muted)' }}>
          {t('languageHint')}
        </p>
      </main>
    </div>
  );
}
