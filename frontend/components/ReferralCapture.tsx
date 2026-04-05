'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { getReferralCode, setReferralCode } from '@/lib/referral';
import { getReferralInterstitial } from '@/lib/api';
import { useTranslations } from 'next-intl';
import type { ReferralInterstitial } from '@/lib/types';
import { X, Gift } from 'lucide-react';

export function ReferralCapture() {
  const searchParams = useSearchParams();
  const t = useTranslations('referrals');
  const [interstitial, setInterstitial] = useState<ReferralInterstitial | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const ref = searchParams.get('ref');
    if (!ref) return;

    // Store the referral code
    if (!getReferralCode()) {
      setReferralCode(ref);
    }

    // Fetch interstitial data to show the banner
    getReferralInterstitial(ref)
      .then((data) => {
        if (data.valid) {
          setInterstitial(data);
        }
      })
      .catch(() => {
        // Silently fail — referral code is still stored
      });
  }, [searchParams]);

  if (!interstitial || dismissed) return null;

  return (
    <div className="fixed top-0 left-0 right-0 z-50 animate-in slide-in-from-top duration-500">
      <div
        className="mx-auto max-w-2xl m-4 rounded-xl shadow-2xl overflow-hidden"
        style={{
          backgroundColor: 'var(--card)',
          border: '2px solid var(--accent)',
        }}
      >
        {/* Dismiss button */}
        <button
          onClick={() => setDismissed(true)}
          className="absolute top-3 right-3 p-1 rounded-full hover:bg-black/10 dark:hover:bg-white/10 transition-colors"
          style={{ color: 'var(--text-muted)' }}
        >
          <X className="w-4 h-4" />
        </button>

        <div className="p-6 space-y-4">
          {/* Header with gift icon */}
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-full bg-primary/10">
              <Gift className="w-6 h-6 text-primary" />
            </div>
            <h2 className="text-lg font-bold" style={{ color: 'var(--text)' }}>
              {t('interstitialTitle')}
            </h2>
          </div>

          {/* Referrer info */}
          {interstitial.display_name && (
            <p className="text-base" style={{ color: 'var(--text-muted)' }}>
              {t('interstitialReferred', { name: interstitial.display_name })}
            </p>
          )}

          {/* Custom message */}
          {interstitial.custom_message && (
            <blockquote
              className="italic border-l-4 border-primary pl-4 py-1"
              style={{ color: 'var(--text-muted)' }}
            >
              &ldquo;{interstitial.custom_message}&rdquo;
            </blockquote>
          )}

          {/* Discount badge */}
          <div className="bg-primary/10 rounded-lg px-4 py-3">
            <p className="text-primary font-semibold text-center">
              {t('interstitialDiscount', {
                percent: interstitial.discount_percent,
                days: interstitial.discount_duration_days,
              })}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
