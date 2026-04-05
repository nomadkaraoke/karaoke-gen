'use client';

import { useEffect, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { getReferralInterstitial } from '@/lib/api';
import { setReferralCode } from '@/lib/referral';
import type { ReferralInterstitial } from '@/lib/types';
import { Gift, ArrowRight } from 'lucide-react';

export function ReferralInterstitialClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const t = useTranslations('referrals');
  const code = searchParams.get('code');

  const [data, setData] = useState<ReferralInterstitial | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!code) {
      router.replace('/');
      return;
    }

    // Store referral code immediately
    setReferralCode(code);

    getReferralInterstitial(code)
      .then((result) => {
        if (result.valid) {
          setData(result);
        } else {
          router.replace('/');
        }
      })
      .catch(() => {
        router.replace('/');
      })
      .finally(() => setLoading(false));
  }, [code, router]);

  if (loading || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg)' }}>
        <div className="animate-spin rounded-full h-8 w-8 border-b-2" style={{ borderColor: 'var(--accent)' }} />
      </div>
    );
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'var(--bg)' }}
    >
      <div
        className="max-w-md w-full rounded-2xl shadow-2xl p-8 text-center space-y-6"
        style={{
          backgroundColor: 'var(--card)',
          border: '1px solid var(--card-border)',
        }}
      >
        {/* Gift icon */}
        <div className="flex justify-center">
          <div className="p-4 rounded-full" style={{ backgroundColor: 'rgba(168, 85, 247, 0.1)' }}>
            <Gift className="w-10 h-10" style={{ color: 'var(--accent)' }} />
          </div>
        </div>

        {/* Title */}
        <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>
          {t('interstitialTitle')}
        </h1>

        {/* Referrer name */}
        {data.display_name && (
          <p className="text-lg" style={{ color: 'var(--text-muted)' }}>
            {t('interstitialReferred', { name: data.display_name })}
          </p>
        )}

        {/* Custom message */}
        {data.custom_message && (
          <blockquote
            className="italic border-l-4 pl-4 py-2 text-left"
            style={{
              color: 'var(--text-muted)',
              borderColor: 'var(--accent)',
            }}
          >
            &ldquo;{data.custom_message}&rdquo;
          </blockquote>
        )}

        {/* Discount badge */}
        <div
          className="rounded-xl px-6 py-4"
          style={{ backgroundColor: 'rgba(168, 85, 247, 0.1)' }}
        >
          <p className="font-semibold text-lg" style={{ color: 'var(--accent)' }}>
            {t('interstitialDiscount', {
              percent: data.discount_percent,
              days: data.discount_duration_days,
            })}
          </p>
        </div>

        {/* CTA */}
        <button
          onClick={() => router.push('/')}
          className="w-full py-4 px-6 rounded-xl font-semibold text-lg flex items-center justify-center gap-2 transition-all hover:scale-[1.02] active:scale-[0.98]"
          style={{
            backgroundColor: 'var(--accent)',
            color: 'white',
          }}
        >
          {t('interstitialCta')}
          <ArrowRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}
