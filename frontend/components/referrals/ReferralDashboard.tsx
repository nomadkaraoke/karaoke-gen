'use client';

import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import {
  getReferralDashboard,
  updateReferralLink,
  startConnectOnboarding,
  getConnectDashboardLink,
  getConnectUpdateLink,
  requestVanityUrl,
} from '@/lib/api';
import type { ReferralDashboard as ReferralDashboardData } from '@/lib/types';
import {
  QrCode, Sparkles, ExternalLink, CheckCircle, AlertCircle,
  Loader2, Banknote, Settings,
} from 'lucide-react';
import QRCodeDialog from './QRCodeDialog';

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default function ReferralDashboard() {
  const t = useTranslations('referrals');
  const searchParams = useSearchParams();
  const [data, setData] = useState<ReferralDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [customMessage, setCustomMessage] = useState('');
  const [qrOpen, setQrOpen] = useState(false);
  const [vanityRequested, setVanityRequested] = useState(false);
  const [vanityError, setVanityError] = useState('');
  const [vanitySubmitting, setVanitySubmitting] = useState(false);
  const [desiredVanity, setDesiredVanity] = useState('');
  const [showVanityForm, setShowVanityForm] = useState(false);
  const [connectStatus, setConnectStatus] = useState<'complete' | 'refresh' | null>(null);
  const [connectLoading, setConnectLoading] = useState(false);
  const [connectError, setConnectError] = useState('');
  const [dashboardLinkLoading, setDashboardLinkLoading] = useState(false);
  const [actionError, setActionError] = useState('');

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

  // Handle Stripe Connect redirect params
  useEffect(() => {
    const connect = searchParams.get('connect');
    if (connect === 'complete') {
      setConnectStatus('complete');
      window.history.replaceState({}, '', window.location.pathname);
    } else if (connect === 'refresh') {
      setConnectStatus('refresh');
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [searchParams]);

  const copyLink = () => {
    if (!data) return;
    const url = `https://nomadkaraoke.com/r/${data.link.code}`;
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
    setConnectLoading(true);
    setConnectError('');
    try {
      const { onboarding_url } = await startConnectOnboarding();
      window.location.href = onboarding_url;
    } catch (err) {
      console.error('Failed to start Connect onboarding:', err);
      setConnectError(t('connectFailed'));
      setConnectLoading(false);
    }
  };

  const handleManagePayouts = async () => {
    setDashboardLinkLoading(true);
    setActionError('');
    try {
      const { url } = await getConnectDashboardLink();
      window.open(url, '_blank');
    } catch (err) {
      console.error('Failed to get dashboard link:', err);
      setActionError(t('connectFailed'));
    } finally {
      setDashboardLinkLoading(false);
    }
  };

  const handleUpdateAccount = async () => {
    setConnectLoading(true);
    setActionError('');
    try {
      const { url } = await getConnectUpdateLink();
      window.location.href = url;
    } catch (err) {
      console.error('Failed to get update link:', err);
      setActionError(t('connectFailed'));
      setConnectLoading(false);
    }
  };

  const handleVanityRequest = async () => {
    if (!data || !desiredVanity.trim()) return;
    setVanityError('');
    setVanitySubmitting(true);
    try {
      await requestVanityUrl(desiredVanity.trim());
      setVanityRequested(true);
      setShowVanityForm(false);
      setDesiredVanity('');
    } catch (err) {
      setVanityError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setVanitySubmitting(false);
    }
  };

  if (loading) {
    return <div className="animate-pulse space-y-4 p-4">
      <div className="h-8 bg-muted rounded w-1/3" />
      <div className="h-24 bg-muted rounded" />
    </div>;
  }

  if (!data) return null;

  const connectAccount = data.stripe_connect_account;

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
            value={`https://nomadkaraoke.com/r/${data.link.code}`}
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
              <div className="space-y-2">
                <div className="flex gap-2 items-center">
                  <span className="text-sm font-mono shrink-0" style={{ color: 'var(--text-muted)' }}>
                    nomadkaraoke.com/r/
                  </span>
                  <input
                    value={desiredVanity}
                    onChange={(e) => { setDesiredVanity(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '')); setVanityError(''); }}
                    placeholder={t('vanityPlaceholder')}
                    className="flex-1 rounded px-3 py-1.5 text-sm font-mono"
                    style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)' }}
                    maxLength={30}
                  />
                  <button
                    onClick={handleVanityRequest}
                    disabled={!desiredVanity.trim() || vanitySubmitting}
                    className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm disabled:opacity-50"
                  >
                    {vanitySubmitting ? '...' : t('vanitySubmit')}
                  </button>
                  <button
                    onClick={() => { setShowVanityForm(false); setDesiredVanity(''); setVanityError(''); }}
                    className="px-3 py-1.5 rounded text-sm"
                    style={{ backgroundColor: 'var(--secondary)' }}
                  >
                    {t('cancel')}
                  </button>
                </div>
                {vanityError && (
                  <p className="text-sm text-red-500">{vanityError}</p>
                )}
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

      {/* Payout & Bank Account section */}
      <div className="rounded-lg p-4 space-y-4" style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}>
        <div className="flex justify-between items-center">
          <h3 className="font-semibold flex items-center gap-2">
            <Banknote className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
            {t('payoutTitle')}
          </h3>
          <div className="text-right">
            <p className="text-lg font-bold">{formatCents(data.pending_balance_cents)}</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('pendingBalance')}</p>
          </div>
        </div>

        {/* Connect complete banner */}
        {connectStatus === 'complete' && (
          <div className="rounded-lg p-3 flex items-center gap-2 border border-green-500/30" style={{ backgroundColor: 'rgba(34, 197, 94, 0.1)' }}>
            <CheckCircle className="w-5 h-5 text-green-500 shrink-0" />
            <p className="text-sm text-green-500">{t('connectComplete')}</p>
          </div>
        )}

        {/* Connect refresh banner */}
        {connectStatus === 'refresh' && !data.stripe_connect_configured && (
          <div className="rounded-lg p-3 flex items-center gap-2 border" style={{ borderColor: 'var(--card-border)', backgroundColor: 'var(--secondary)' }}>
            <AlertCircle className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('connectRefresh')}</p>
          </div>
        )}

        {data.stripe_connect_configured ? (
          /* Connected state — show account status and management */
          <div className="space-y-3">
            {/* Account status */}
            <div className="rounded-lg p-3 space-y-2" style={{ backgroundColor: 'var(--secondary)' }}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full ${connectAccount?.payouts_enabled ? 'bg-green-500' : 'bg-yellow-500'}`} />
                  <span className="text-sm font-medium">
                    {connectAccount?.payouts_enabled ? t('connectStatusActive') : t('connectStatusPending')}
                  </span>
                </div>
                {connectAccount?.email && (
                  <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>
                    {connectAccount.email}
                  </span>
                )}
              </div>

              {connectAccount && !connectAccount.details_submitted && (
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {t('connectDetailsNeeded')}
                </p>
              )}
            </div>

            {/* Payout info */}
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('payoutThreshold')}</p>

            {/* Management buttons */}
            <div className="flex flex-wrap gap-2">
              <button
                onClick={handleManagePayouts}
                disabled={dashboardLinkLoading}
                className="px-3 py-1.5 rounded text-sm border flex items-center gap-1.5 hover:bg-white/5 transition-colors disabled:opacity-50"
                style={{ borderColor: 'var(--card-border)', color: 'var(--text)' }}
              >
                {dashboardLinkLoading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <ExternalLink className="w-3.5 h-3.5" />
                )}
                {t('managePayouts')}
              </button>
              <button
                onClick={handleUpdateAccount}
                disabled={connectLoading}
                className="px-3 py-1.5 rounded text-sm border flex items-center gap-1.5 hover:bg-white/5 transition-colors disabled:opacity-50"
                style={{ borderColor: 'var(--card-border)', color: 'var(--text-muted)' }}
              >
                {connectLoading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Settings className="w-3.5 h-3.5" />
                )}
                {t('updateBankAccount')}
              </button>
            </div>
            {actionError && (
              <p className="text-sm text-red-500">{actionError}</p>
            )}
          </div>
        ) : (
          /* Not connected — show setup prompt */
          <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--secondary)' }}>
            <p className="text-sm">{t('connectDescription')}</p>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('connectStripeNote')}</p>
            <button
              onClick={handleConnectBank}
              disabled={connectLoading}
              className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center gap-2 disabled:opacity-70"
            >
              {connectLoading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('connectBankLoading')}
                </>
              ) : (
                t('connectBank')
              )}
            </button>
            {connectError && (
              <p className="text-sm text-red-500">{connectError}</p>
            )}
          </div>
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
        referralUrl={`https://nomadkaraoke.com/r/${data.link.code}`}
        open={qrOpen}
        onOpenChange={setQrOpen}
      />
    </div>
  );
}
