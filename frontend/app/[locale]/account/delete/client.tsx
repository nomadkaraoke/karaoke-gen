'use client';

import { useCallback, useEffect, useState } from 'react';
import { notFound } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Loader2 } from 'lucide-react';
import { Link } from '@/i18n/routing';
import { Button } from '@/components/ui/button';
import { AppHeader } from '@/components/app-header';
import { AuthDialog } from '@/components/auth/AuthDialog';
import { api, getAccessToken } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import type { DeletionBlockedCode, DeletionPreview } from '@/lib/types';

const SUPPORT_EMAIL = 'support@nomadkaraoke.com';

type Phase =
  | { kind: 'loading' }
  | { kind: 'signedOut'; afterDelete?: boolean }
  | { kind: 'hidden' }
  | { kind: 'ready'; preview: DeletionPreview }
  | { kind: 'confirming'; preview: DeletionPreview }
  | { kind: 'deleting'; preview: DeletionPreview }
  | { kind: 'deleted' }
  | { kind: 'blocked'; code: DeletionBlockedCode | null }
  | { kind: 'error'; retryable: boolean; preview: DeletionPreview | null };

const BLOCKED_KEYS: Record<DeletionBlockedCode, string> = {
  admin: 'blockedAdmin',
  referral_balance: 'blockedReferralBalance',
  connect_account: 'blockedConnectAccount',
  order_in_progress: 'blockedOrderInProgress',
};

function blockedCode(code: unknown): DeletionBlockedCode | null {
  // An unknown code (a newer backend) falls back to the generic support copy.
  return typeof code === 'string' && Object.prototype.hasOwnProperty.call(BLOCKED_KEYS, code)
    ? (code as DeletionBlockedCode)
    : null;
}

function statusOf(err: unknown): number | undefined {
  return err && typeof err === 'object' && 'status' in err ? (err as { status: number }).status : undefined;
}

export function DeleteAccountClient() {
  const t = useTranslations('accountDelete');
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [showAuth, setShowAuth] = useState(false);

  const load = useCallback(async () => {
    if (!getAccessToken()) {
      setPhase({ kind: 'signedOut' });
      return;
    }
    setPhase({ kind: 'loading' });
    try {
      const me = await api.getCurrentUser();
      if (me.capabilities?.account_deletion !== true) {
        setPhase({ kind: 'hidden' });
        return;
      }
      const preview = await api.getDeletionPreview();
      setPhase(preview.blocked ? { kind: 'blocked', code: blockedCode(preview.blocked) } : { kind: 'ready', preview });
    } catch (err) {
      const status = statusOf(err);
      if (status === 401) setPhase({ kind: 'signedOut' });
      else if (status === 404) setPhase({ kind: 'hidden' });
      else setPhase({ kind: 'error', retryable: false, preview: null });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const finishDeleted = useCallback(async () => {
    // Same as sign-out: clears the stored session token and the auth store
    // (the logout call itself is best-effort and ignored if the session is gone).
    await useAuth.getState().logout();
    setPhase({ kind: 'deleted' });
  }, []);

  const confirmDelete = useCallback(
    async (preview: DeletionPreview) => {
      setPhase({ kind: 'deleting', preview });
      try {
        await api.deleteAccount();
        await finishDeleted();
      } catch (err) {
        const status = statusOf(err);
        if (status === 401) {
          // The session is gone: either a retry after a lost 200 (deleted) or the
          // session simply expired. We can't tell, so sign out and say both.
          await useAuth.getState().logout();
          setPhase({ kind: 'signedOut', afterDelete: true });
        } else if (status === 409) {
          setPhase({ kind: 'blocked', code: blockedCode((err as { data?: { code?: unknown } }).data?.code) });
        } else {
          // 503 {step} is retryable; anything else (network, 5xx) is treated the same:
          // each backend step is idempotent, so a retry resumes the deletion.
          setPhase({ kind: 'error', retryable: true, preview });
        }
      }
    },
    [finishDeleted],
  );

  if (phase.kind === 'hidden') {
    notFound();
    return null;
  }

  const preview =
    phase.kind === 'ready' || phase.kind === 'confirming' || phase.kind === 'deleting'
      ? phase.preview
      : phase.kind === 'error'
        ? phase.preview
        : null;

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--background)' }}>
      <AppHeader />
      <main className="container mx-auto max-w-2xl px-4 pt-6 pb-12" data-testid="delete-account-page">
        <h1 className="text-2xl font-semibold mb-4" style={{ color: 'var(--text)' }}>
          {t('title')}
        </h1>

        {phase.kind === 'loading' && (
          <div className="flex items-center gap-2" data-testid="delete-account-loading" style={{ color: 'var(--text-muted)' }}>
            <Loader2 className="w-5 h-5 animate-spin" />
            {t('loading')}
          </div>
        )}

        {phase.kind === 'signedOut' && (
          <div className="space-y-4" data-testid="delete-account-signed-out">
            <p data-testid={phase.afterDelete ? 'delete-account-signed-out-after-delete' : undefined}>
              {phase.afterDelete ? t('signedOutMaybeDeleted') : t('signInPrompt')}
            </p>
            <Button onClick={() => setShowAuth(true)} data-testid="delete-account-sign-in">
              {t('signInButton')}
            </Button>
            <AuthDialog
              open={showAuth}
              onClose={() => setShowAuth(false)}
              onSuccess={() => {
                setShowAuth(false);
                load();
              }}
            />
          </div>
        )}

        {phase.kind === 'deleted' && (
          <div className="space-y-4" data-testid="delete-account-deleted">
            <h2 className="text-xl font-medium">{t('deletedTitle')}</h2>
            <p>{t('deletedBody')}</p>
            <Link href="/" className="underline" data-testid="delete-account-home">
              {t('backHome')}
            </Link>
          </div>
        )}

        {phase.kind === 'blocked' && (
          <div className="space-y-3" role="alert" data-testid="delete-account-blocked">
            {phase.code && <p>{t(BLOCKED_KEYS[phase.code])}</p>}
            <p>{t('blockedContact', { email: SUPPORT_EMAIL })}</p>
            <a href={`mailto:${SUPPORT_EMAIL}`} className="underline" data-testid="delete-account-support-link">
              {SUPPORT_EMAIL}
            </a>
          </div>
        )}

        {phase.kind === 'error' && (
          <div className="space-y-3" role="alert" data-testid="delete-account-error">
            <p>{phase.retryable ? t('retryPartway') : t('genericError', { email: SUPPORT_EMAIL })}</p>
            <Button
              onClick={() => (phase.preview ? confirmDelete(phase.preview) : load())}
              data-testid="delete-account-retry"
            >
              {t('retryButton')}
            </Button>
          </div>
        )}

        {preview && phase.kind !== 'error' && (
          <div className="space-y-6">
            <p>{t('intro')}</p>

            <section>
              <h2 className="font-medium mb-2">{t('deletedHeading')}</h2>
              <ul className="list-disc pl-6 space-y-1">
                <li>{t('deletedPersonal')}</li>
                <li>{t('deletedUploads')}</li>
                <li>{t('deletedMusicData')}</li>
                <li>{t('deletedNightLinks')}</li>
              </ul>
            </section>

            <section>
              <h2 className="font-medium mb-2">{t('keptHeading')}</h2>
              <ul className="list-disc pl-6 space-y-1">
                <li>{t('keptRecords')}</li>
                <li>{t('keptYoutube')}</li>
              </ul>
            </section>

            {(preview.credits > 0 || preview.jobs_in_progress > 0) && (
              <ul className="space-y-1 font-medium" data-testid="delete-account-losses">
                {preview.credits > 0 && (
                  <li data-testid="delete-account-credits">{t('creditsLost', { count: preview.credits })}</li>
                )}
                {preview.jobs_in_progress > 0 && (
                  <li data-testid="delete-account-jobs">{t('jobsCancelled', { count: preview.jobs_in_progress })}</li>
                )}
              </ul>
            )}

            {phase.kind === 'ready' && (
              <Button
                variant="destructive"
                onClick={() => setPhase({ kind: 'confirming', preview: phase.preview })}
                data-testid="delete-account-start"
              >
                {t('deleteButton')}
              </Button>
            )}

            {(phase.kind === 'confirming' || phase.kind === 'deleting') && (
              <div className="space-y-3 border rounded-md p-4" role="alertdialog" data-testid="delete-account-confirm-panel">
                <h2 className="font-medium">{t('confirmTitle')}</h2>
                <p>{t('confirmBody')}</p>
                <div className="flex gap-3">
                  <Button
                    variant="destructive"
                    disabled={phase.kind === 'deleting'}
                    onClick={() => confirmDelete(phase.preview)}
                    data-testid="delete-account-confirm"
                  >
                    {phase.kind === 'deleting' ? t('deleting') : t('confirmButton')}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={phase.kind === 'deleting'}
                    onClick={() => setPhase({ kind: 'ready', preview: phase.preview })}
                    data-testid="delete-account-cancel"
                  >
                    {t('cancelButton')}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
