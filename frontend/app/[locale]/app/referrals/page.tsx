'use client';

import ReferralDashboard from '@/components/referrals/ReferralDashboard';
import { AppHeader } from '@/components/app-header';
import { Link } from '@/i18n/routing';
import { ChevronLeft } from 'lucide-react';
import { useTranslations } from 'next-intl';

export default function ReferralsPage() {
  const t = useTranslations('referrals');

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--background)' }}>
      <AppHeader />
      <main className="container mx-auto max-w-4xl px-4 py-6">
        <Link
          href="/app"
          className="inline-flex items-center gap-1 text-sm mb-4 hover:underline"
          style={{ color: 'var(--text-muted)' }}
        >
          <ChevronLeft className="w-4 h-4" />
          {t('backToDashboard')}
        </Link>
        <ReferralDashboard />
      </main>
    </div>
  );
}
