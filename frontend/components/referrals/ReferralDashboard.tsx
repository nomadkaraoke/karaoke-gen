'use client';

import { useEffect, useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import {
  getReferralDashboard,
  updateReferralLink,
  startConnectOnboarding,
} from '@/lib/api';
import type { ReferralDashboard as ReferralDashboardData } from '@/lib/types';
import { QrCode, Gift, DollarSign, Users, Clock, Share2, Sparkles, ExternalLink } from 'lucide-react';
import QRCodeDialog from './QRCodeDialog';

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default function ReferralDashboard() {
  const t = useTranslations('referrals');
  const [data, setData] = useState<ReferralDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [customMessage, setCustomMessage] = useState('');
  const [qrOpen, setQrOpen] = useState(false);
  const [vanityRequested, setVanityRequested] = useState(false);
  const [desiredVanity, setDesiredVanity] = useState('');
  const [showVanityForm, setShowVanityForm] = useState(false);

  const fetchDashboard = useCallback(async () => {
    try {
      const result = await getReferralDashboard();
      setData(result);
      setDisplayName(result.link.display_name || '');
      setCustomMessage(result.link.custom_message || '');
    } catch (err) {
      console.error('Failed to load referral dashboard:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  const copyLink = () => {
    if (!data) return;
    const url = `${window.location.origin}/r/${data.link.code}`;
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const saveProfile = async () => {
    try {
      await updateReferralLink({ display_name: displayName, custom_message: customMessage });
      setEditing(false);
      fetchDashboard();
    } catch (err) {
      console.error('Failed to save:', err);
    }
  };

  const handleConnectBank = async () => {
    try {
      const { onboarding_url } = await startConnectOnboarding();
      window.location.href = onboarding_url;
    } catch (err) {
      console.error('Failed to start Connect onboarding:', err);
    }
  };

  const handleVanityRequest = () => {
    if (!data || !desiredVanity.trim()) return;
    const subject = encodeURIComponent(`Vanity referral URL request: ${desiredVanity.trim()}`);
    const body = encodeURIComponent(
      `Hi!\n\nI'd like to request a custom referral URL.\n\nDesired URL: ${window.location.origin}/r/${desiredVanity.trim()}\nCurrent code: ${data.link.code}\n\nThanks!`
    );
    window.open(`mailto:andrew@nomadkaraoke.com?subject=${subject}&body=${body}`, '_self');
    setVanityRequested(true);
    setShowVanityForm(false);
    setDesiredVanity('');
  };

  if (loading) {
    return <div className="animate-pulse space-y-4 p-4">
      <div className="h-8 bg-muted rounded w-1/3" />
      <div className="h-24 bg-muted rounded" />
    </div>;
  }

  if (!data) return null;

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">{t('title')}</h2>

      {/* How It Works */}
      <div className="rounded-lg p-5 space-y-4" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
        <h3 className="font-semibold text-lg flex items-center gap-2">
          <Sparkles className="w-5 h-5" style={{ color: 'var(--accent)' }} />
          {t('howItWorksTitle')}
        </h3>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          {t('howItWorksIntro')}
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2">
          <div className="flex gap-3">
            <div className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" style={{ backgroundColor: 'var(--accent)', color: 'var(--background)' }}>1</div>
            <div>
              <p className="text-sm font-medium">{t('howStep1Title')}</p>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{t('howStep1Desc')}</p>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" style={{ backgroundColor: 'var(--accent)', color: 'var(--background)' }}>2</div>
            <div>
              <p className="text-sm font-medium">{t('howStep2Title')}</p>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {t('howStep2Desc', { discount: data.link.discount_percent, days: data.link.discount_duration_days })}
              </p>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" style={{ backgroundColor: 'var(--accent)', color: 'var(--background)' }}>3</div>
            <div>
              <p className="text-sm font-medium">{t('howStep3Title')}</p>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {t('howStep3Desc', { kickback: data.link.kickback_percent, days: data.link.earning_duration_days })}
              </p>
            </div>
          </div>
        </div>

        <div className="rounded-md p-3 text-sm" style={{ backgroundColor: 'var(--secondary)' }}>
          <p className="font-medium mb-1">{t('howPayoutTitle')}</p>
          <p style={{ color: 'var(--text-muted)' }}>{t('howPayoutDesc')}</p>
        </div>
      </div>

      {/* Referral Link */}
      <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--card)', borderColor: 'var(--card-border)', border: '1px solid' }}>
        <h3 className="font-semibold">{t('yourLink')}</h3>
        <div className="flex gap-2">
          <input
            readOnly
            value={`${typeof window !== 'undefined' ? window.location.origin : ''}/r/${data.link.code}`}
            className="flex-1 rounded px-3 py-2 text-sm font-mono"
            style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)' }}
          />
          <button onClick={copyLink} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
            {copied ? t('copied') : t('copyLink')}
          </button>
          <button
            type="button"
            onClick={() => setQrOpen(true)}
            className="px-3 py-2 rounded text-sm border flex items-center gap-1.5"
            style={{ borderColor: 'var(--card-border)', color: 'var(--text)' }}
          >
            <QrCode className="w-4 h-4" />
            {t('qrOrFlyer')}
          </button>
        </div>

        {/* Vanity URL request */}
        {!data.link.is_vanity && (
          <div className="pt-1">
            {vanityRequested ? (
              <p className="text-sm" style={{ color: 'var(--accent)' }}>
                {t('vanityRequested')}
              </p>
            ) : showVanityForm ? (
              <div className="flex gap-2 items-center">
                <span className="text-sm font-mono shrink-0" style={{ color: 'var(--text-muted)' }}>
                  {typeof window !== 'undefined' ? window.location.origin : ''}/r/
                </span>
                <input
                  value={desiredVanity}
                  onChange={(e) => setDesiredVanity(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                  placeholder={t('vanityPlaceholder')}
                  className="flex-1 rounded px-3 py-1.5 text-sm font-mono"
                  style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)' }}
                  maxLength={30}
                />
                <button
                  onClick={handleVanityRequest}
                  disabled={!desiredVanity.trim()}
                  className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm disabled:opacity-50"
                >
                  {t('vanitySubmit')}
                </button>
                <button
                  onClick={() => { setShowVanityForm(false); setDesiredVanity(''); }}
                  className="px-3 py-1.5 rounded text-sm"
                  style={{ backgroundColor: 'var(--secondary)' }}
                >
                  {t('cancel')}
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowVanityForm(true)}
                className="text-sm hover:underline"
                style={{ color: 'var(--text-muted)' }}
              >
                {t('vanityRequest')}
              </button>
            )}
          </div>
        )}

        {editing ? (
          <div className="space-y-2">
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t('displayName')}
              className="w-full rounded px-3 py-2 text-sm"
              style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)' }}
            />
            <textarea
              value={customMessage}
              onChange={(e) => setCustomMessage(e.target.value.slice(0, 200))}
              placeholder={t('customMessagePlaceholder')}
              rows={3}
              className="w-full rounded px-3 py-2 text-sm"
              style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)' }}
            />
            <div className="flex gap-2">
              <button onClick={saveProfile} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
                {t('save')}
              </button>
              <button onClick={() => setEditing(false)} className="px-4 py-2 rounded text-sm" style={{ backgroundColor: 'var(--secondary)' }}>
                {t('cancel')}
              </button>
            </div>
          </div>
        ) : (
          <button onClick={() => setEditing(true)} className="text-sm text-primary hover:underline">
            {t('editProfile')}
          </button>
        )}
      </div>

      {/* Link Terms */}
      <div className="rounded-lg p-4 grid grid-cols-2 md:grid-cols-4 gap-4 text-center" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
        <div>
          <p className="text-lg font-bold" style={{ color: 'var(--accent)' }}>{data.link.discount_percent}%</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('termDiscount')}</p>
        </div>
        <div>
          <p className="text-lg font-bold">{data.link.discount_duration_days}d</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('termDiscountWindow')}</p>
        </div>
        <div>
          <p className="text-lg font-bold" style={{ color: 'var(--accent)' }}>{data.link.kickback_percent}%</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('termKickback')}</p>
        </div>
        <div>
          <p className="text-lg font-bold">{data.link.earning_duration_days}d</p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('termEarningWindow')}</p>
        </div>
      </div>

      {/* Stats */}
      <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
        <h3 className="font-semibold mb-3">{t('stats')}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-2xl font-bold">{data.link.stats.clicks}</p>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('clicks')}</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{data.link.stats.signups}</p>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('signups')}</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{data.link.stats.purchases}</p>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('purchases')}</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{formatCents(data.total_earned_cents)}</p>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('totalEarned')}</p>
          </div>
        </div>
      </div>

      {/* Payout section */}
      <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
        <div className="flex justify-between items-center">
          <h3 className="font-semibold">{t('earnings')}</h3>
          <div className="text-right">
            <p className="text-lg font-bold">{formatCents(data.pending_balance_cents)}</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('pendingBalance')}</p>
          </div>
        </div>

        {!data.stripe_connect_configured ? (
          <div className="rounded-lg p-4 space-y-2" style={{ backgroundColor: 'var(--secondary)' }}>
            <p className="text-sm">{t('connectDescription')}</p>
            <button onClick={handleConnectBank} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
              {t('connectBank')}
            </button>
          </div>
        ) : (
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('payoutThreshold')}</p>
        )}
      </div>

      {/* Recent earnings */}
      <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
        <h3 className="font-semibold mb-3">{t('recentEarnings')}</h3>
        {data.recent_earnings.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('noEarnings')}</p>
        ) : (
          <div className="space-y-2">
            {data.recent_earnings.map((e) => (
              <div key={e.id} className="flex justify-between text-sm">
                <span style={{ color: 'var(--text-muted)' }}>{e.referred_email}</span>
                <span className="font-mono">
                  {formatCents(e.earning_amount_cents)}
                  <span className={`ml-2 text-xs ${e.status === 'paid' ? 'text-green-500' : 'text-yellow-500'}`}>
                    {e.status}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent payouts */}
      {data.recent_payouts.length > 0 && (
        <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
          <h3 className="font-semibold mb-3">{t('recentPayouts')}</h3>
          <div className="space-y-2">
            {data.recent_payouts.map((p) => (
              <div key={p.id} className="flex justify-between text-sm">
                <span style={{ color: 'var(--text-muted)' }}>{new Date(p.created_at).toLocaleDateString()}</span>
                <span className="font-mono">{formatCents(p.amount_cents)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <QRCodeDialog
        referralUrl={`${typeof window !== 'undefined' ? window.location.origin : ''}/r/${data.link.code}`}
        open={qrOpen}
        onOpenChange={setQrOpen}
      />
    </div>
  );
}
