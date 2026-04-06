'use client';

import { Suspense } from 'react';
import ReferralDashboard from '@/components/referrals/ReferralDashboard';
import { AppHeader } from '@/components/app-header';
import { Link } from '@/i18n/routing';
import { ChevronLeft, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';

export default function ReferralsPage() {
  const t = useTranslations('referrals');

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--background)' }}>
      <AppHeader />
      <main className="container mx-auto max-w-4xl px-4 pt-20 pb-6">
        <Link
          href="/app"
          className="inline-flex items-center gap-1 text-sm mb-4 hover:underline"
          style={{ color: 'var(--text-muted)' }}
        >
          <ChevronLeft className="w-4 h-4" />
          {t('backToDashboard')}
        </Link>
        <Suspense fallback={<Loader2 className="w-6 h-6 animate-spin mx-auto mt-8" style={{ color: 'var(--text-muted)' }} />}>
          <ReferralDashboard />
        </Suspense>
      </main>
    </div>
  );
}
