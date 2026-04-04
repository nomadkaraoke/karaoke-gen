'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { getReferralInterstitial } from '@/lib/api';
import { setReferralCode } from '@/lib/referral';
import type { ReferralInterstitial } from '@/lib/types';

export function ReferralInterstitialClient() {
  const params = useParams();
  const router = useRouter();
  const t = useTranslations('referrals');

  // Extract code from catch-all slug: /r/CODE → code = ["CODE"]
  const codeSegments = params.code as string[] | undefined;
  const code = codeSegments?.[0];

  const [data, setData] = useState<ReferralInterstitial | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!code) {
      // No code provided — redirect to landing
      router.push('/');
      return;
    }

    async function fetchData() {
      try {
        const result = await getReferralInterstitial(code!);
        setData(result);
        if (result.valid) {
          setReferralCode(code!);
        }
      } catch {
        setData({ code: code!, valid: false, display_name: null, custom_message: null, discount_percent: 0, discount_duration_days: 0 });
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [code, router]);

  const handleGetStarted = () => {
    router.push('/');
  };

  if (!code || loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: 'var(--background)' }}>
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (!data?.valid) {
    router.push('/');
    return null;
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ backgroundColor: 'var(--background)' }}>
      <div className="max-w-md w-full rounded-xl shadow-lg p-8 text-center space-y-6" style={{ backgroundColor: 'var(--card)', borderColor: 'var(--card-border)' }}>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>
          {t('interstitialTitle')}
        </h1>

        {data.display_name && (
          <p className="text-lg" style={{ color: 'var(--text-muted)' }}>
            {t('interstitialReferred', { name: data.display_name })}
          </p>
        )}

        {data.custom_message && (
          <blockquote className="italic border-l-4 border-primary pl-4 text-left" style={{ color: 'var(--text-muted)' }}>
            &ldquo;{data.custom_message}&rdquo;
          </blockquote>
        )}

        <div className="bg-primary/10 rounded-lg p-4">
          <p className="text-primary font-semibold">
            {t('interstitialDiscount', {
              percent: data.discount_percent,
              days: data.discount_duration_days,
            })}
          </p>
        </div>

        <button
          onClick={handleGetStarted}
          className="w-full py-3 px-6 bg-primary text-primary-foreground rounded-lg font-semibold hover:bg-primary/90 transition-colors"
        >
          {t('interstitialCta')}
        </button>
      </div>
    </div>
  );
}
