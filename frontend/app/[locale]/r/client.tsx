'use client';

import { useEffect, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { getReferralInterstitial } from '@/lib/api';
import { setReferralCode } from '@/lib/referral';
import { useAuth } from '@/lib/auth';
import { getAccessToken } from '@/lib/api';
import type { ReferralInterstitial } from '@/lib/types';
import { Gift, ArrowRight, Mail, CheckCircle, Loader2 } from 'lucide-react';

export function ReferralInterstitialClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const t = useTranslations('referrals');
  const { sendMagicLink } = useAuth();
  const code = searchParams.get('code');

  const [data, setData] = useState<ReferralInterstitial | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    if (!code) {
      router.replace('/');
      return;
    }

    // Store referral code immediately
    setReferralCode(code);

    // Check if already logged in
    if (getAccessToken()) {
      setIsLoggedIn(true);
    }

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const trimmed = email.trim().toLowerCase();
    if (!trimmed || !trimmed.includes('@') || !trimmed.includes('.')) {
      setError('Please enter a valid email address');
      return;
    }

    setSending(true);
    try {
      const success = await sendMagicLink(trimmed);
      if (success) {
        setSent(true);
      } else {
        setError('Failed to send magic link. Please try again.');
      }
    } catch {
      setError('Something went wrong. Please try again.');
    } finally {
      setSending(false);
    }
  };

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

        {/* Already logged in */}
        {isLoggedIn ? (
          <div className="space-y-4">
            <div className="flex items-center justify-center gap-2" style={{ color: 'var(--accent)' }}>
              <CheckCircle className="w-5 h-5" />
              <p className="font-medium">Your discount will be applied on your next login</p>
            </div>
            <button
              onClick={() => router.push('/app')}
              className="w-full py-4 px-6 rounded-xl font-semibold text-lg flex items-center justify-center gap-2 transition-all hover:scale-[1.02] active:scale-[0.98]"
              style={{ backgroundColor: 'var(--accent)', color: 'white' }}
            >
              Go to App
              <ArrowRight className="w-5 h-5" />
            </button>
          </div>
        ) : sent ? (
          /* Magic link sent confirmation */
          <div className="space-y-4">
            <div className="flex items-center justify-center gap-2" style={{ color: 'var(--accent)' }}>
              <CheckCircle className="w-5 h-5" />
              <p className="font-medium">Check your email!</p>
            </div>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              We sent a sign-in link to <strong>{email}</strong>. Click it to claim your {data.discount_percent}% discount.
            </p>
          </div>
        ) : (
          /* Email signup form */
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="relative">
              <Mail
                className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5"
                style={{ color: 'var(--text-muted)' }}
              />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Enter your email"
                className="w-full pl-11 pr-4 py-4 rounded-xl text-base"
                style={{
                  backgroundColor: 'var(--secondary)',
                  color: 'var(--text)',
                  border: '1px solid var(--card-border)',
                }}
                autoFocus
              />
            </div>
            {error && (
              <p className="text-sm text-red-400">{error}</p>
            )}
            <button
              type="submit"
              disabled={sending}
              className="w-full py-4 px-6 rounded-xl font-semibold text-lg flex items-center justify-center gap-2 transition-all hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50 disabled:hover:scale-100"
              style={{ backgroundColor: 'var(--accent)', color: 'white' }}
            >
              {sending ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Sending...
                </>
              ) : (
                <>
                  Claim Your Discount
                  <ArrowRight className="w-5 h-5" />
                </>
              )}
            </button>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              We&apos;ll send you a magic link to sign in. No password needed.
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
