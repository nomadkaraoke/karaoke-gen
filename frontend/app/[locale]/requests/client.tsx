'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from '@/i18n/routing';
import { useTranslations } from 'next-intl';
import {
  ChevronUp,
  ChevronDown,
  Music,
  Youtube,
  Loader2,
  Mail,
  CheckCircle,
  Sparkles,
  ArrowRight,
} from 'lucide-react';
import { api, getAccessToken } from '@/lib/api';
import { ApiError } from '@/lib/api-error';
import type { CommunityCheckResponse } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { CommunityVersionBanner } from '@/components/job/CommunityVersionBanner';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import type { BoardResponse, SongRequestPublic } from '@/lib/types';

const BOARD_PURPOSE = 'requests_board';
const REQUESTS_HOST = 'requests.nomadkaraoke.com';
// Read by the requests-host Worker (infrastructure/cloudflare/requests-host) to pick the
// board language at "/", so the shareable URL stays bare after switching language.
const REQUESTS_LOCALE_COOKIE = 'nk_requests_locale';

// Deep link to one request (?request=<id>), e.g. from the YouTube comment bot's
// reply. After board sign-in the magic link returns with &upvote=1 so the
// commenter's upvote is cast on arrival (which also claims a bot-added request).
const REQUEST_ID_RE = /^[A-Za-z0-9_-]{4,64}$/;

function readDeepLink(): { requestId: string | null; upvote: boolean } {
  const params = new URLSearchParams(window.location.search);
  const id = params.get('request');
  return { requestId: id && REQUEST_ID_RE.test(id) ? id : null, upvote: params.get('upvote') === '1' };
}

function dropUpvoteParam() {
  const url = new URL(window.location.href);
  url.searchParams.delete('upvote');
  window.history.replaceState(window.history.state, '', url.toString());
}

// What the confirmation dialog is asking about. Votes are scarce (one a day) and removing
// a request's last up-vote deletes it, so both get an explicit "are you sure".
type PendingConfirm =
  | { kind: 'spend'; req: SongRequestPublic; direction: 'up' | 'down'; moving: boolean }
  | { kind: 'delete'; req: SongRequestPublic; direction: 'up' | 'down' }
  | { kind: 'submit' };

function selectLocaleOnRequestsHost(code: string) {
  document.cookie = `${REQUESTS_LOCALE_COOKIE}=${code}; path=/; max-age=31536000; secure; samesite=lax`;
  window.location.assign('/');
}

export function RequestsBoardClient() {
  const t = useTranslations('requests');
  const router = useRouter();
  const { user, sendMagicLink, fetchUser } = useAuth();

  const [board, setBoard] = useState<BoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Submission form
  const [artist, setArtist] = useState('');
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [correctionNote, setCorrectionNote] = useState('');
  // Soft "an existing community karaoke version exists" check (non-blocking).
  const [communityData, setCommunityData] = useState<CommunityCheckResponse | null>(null);
  const [communityDismissed, setCommunityDismissed] = useState(false);

  // Sign-in
  const [email, setEmail] = useState('');
  const [sendingLink, setSendingLink] = useState(false);
  const [linkSent, setLinkSent] = useState(false);

  const [pendingVoteId, setPendingVoteId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  // Warm thank-you (with the credit-pack pitch) shown after a request or an up-vote.
  const [thanks, setThanks] = useState<'submitted' | 'voted' | null>(null);
  const [notice, setNotice] = useState('');

  const isSignedIn = !!user;
  const [onRequestsHost, setOnRequestsHost] = useState(false);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [autoUpvote, setAutoUpvote] = useState(false);
  const [deepLinkVoted, setDeepLinkVoted] = useState(false);
  useEffect(() => {
    setOnRequestsHost(window.location.hostname === REQUESTS_HOST);
    const link = readDeepLink();
    setFocusId(link.requestId);
    setAutoUpvote(link.upvote && !!link.requestId);
  }, []);
  const focusReq = focusId ? board?.requests.find((r) => r.id === focusId) ?? null : null;

  // Guards against an older refresh resolving after a newer one and clobbering state
  // (refresh can fire concurrently from hydration + submit + vote).
  const refreshGeneration = useRef(0);
  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    try {
      const data = await api.getRequestsBoard();
      if (generation === refreshGeneration.current) setBoard(data);
    } catch {
      if (generation === refreshGeneration.current) setError(t('errorGeneric'));
    } finally {
      if (generation === refreshGeneration.current) setLoading(false);
    }
  }, [t]);

  const errorCode = (e: unknown): string | undefined =>
    e instanceof ApiError ? (e.data?.detail?.code as string | undefined) : undefined;

  const errorMessage = (e: unknown): string => {
    switch (errorCode(e)) {
      case 'submission_limit':
        return t('limitSubmission');
      case 'vote_already_used':
        return t('voteSpentCannotSubmit');
      default:
        return t('errorGeneric');
    }
  };

  useEffect(() => {
    // Hydrate the user if we have a stored session token but no user yet.
    if (!user && getAccessToken()) {
      fetchUser().catch(() => {});
    }
    refresh();
  }, [refresh, fetchUser, user]);

  // Scroll the deep-linked request into view once the board has loaded.
  const scrolledToFocus = useRef(false);
  useEffect(() => {
    if (!focusReq || scrolledToFocus.current) return;
    scrolledToFocus.current = true;
    document.getElementById(`request-${focusReq.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusReq]);

  // Back from the magic link with &upvote=1: cast the upvote once (unless they
  // already upvoted it today), then drop the param so a reload can't toggle it off.
  useEffect(() => {
    if (!autoUpvote || !isSignedIn || !focusReq) return;
    setAutoUpvote(false);
    dropUpvoteParam();
    if (focusReq.your_vote === 1) {
      setDeepLinkVoted(true);
      return;
    }
    if (board?.voted_today) {
      // Today's single vote is already on another request: don't silently move it.
      setError(t('voteSpentCannotSubmit'));
      return;
    }
    api
      .voteSongRequest(focusReq.id, 'up')
      .then(() => {
        setDeepLinkVoted(true);
        setThanks('voted');
        return refresh();
      })
      .catch((e) => setError(errorMessage(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoUpvote, isSignedIn, focusReq, refresh, t, board?.voted_today]);

  // Debounced KaraokeNerds check as the user types artist/title (signed-in only —
  // the check endpoint requires auth). Soft/non-blocking: we just surface existing
  // community versions so they can watch one on YouTube instead if they prefer.
  useEffect(() => {
    if (!isSignedIn) return;
    const a = artist.trim();
    const ti = title.trim();
    if (!a || !ti) {
      setCommunityData(null);
      return;
    }
    setCommunityDismissed(false);
    let cancelled = false;
    const handle = setTimeout(() => {
      api
        .checkCommunityVersions(a, ti)
        .then((res) => {
          if (!cancelled) setCommunityData(res);
        })
        .catch(() => {
          /* non-blocking — never let the check disrupt requesting */
        });
    }, 500);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [artist, title, isSignedIn]);

  const handleSendLink = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    const trimmed = email.trim().toLowerCase();
    if (!trimmed.includes('@') || !trimmed.includes('.')) {
      setError(t('invalidEmail'));
      return;
    }
    setSendingLink(true);
    try {
      const ok = await sendMagicLink(trimmed, focusReq ? `${BOARD_PURPOSE}:${focusReq.id}` : BOARD_PURPOSE);
      setLinkSent(ok);
      if (!ok) setError(t('errorGeneric'));
    } catch {
      setError(t('errorGeneric'));
    } finally {
      setSendingLink(false);
    }
  };

  // Submitting spends today's single vote, so ask first (the daily vote can't be wasted by
  // accident). The form is disabled outright when today's vote is already spent.
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!artist.trim() || !title.trim()) return;
    setError('');
    setNotice('');
    setConfirm({ kind: 'submit' });
  };

  const doSubmit = async () => {
    setConfirm(null);
    setError('');
    setCorrectionNote('');
    setSubmitting(true);
    try {
      const res = await api.submitSongRequest(artist.trim(), title.trim());
      if (res.was_corrected) {
        setCorrectionNote(t('correctedNote', { artist: res.canonical_artist, title: res.canonical_title }));
      }
      setArtist('');
      setTitle('');
      setCommunityData(null);
      setThanks('submitted');
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const castVote = async (req: SongRequestPublic, direction: 'up' | 'down', confirmDelete = false) => {
    setConfirm(null);
    setError('');
    setNotice('');
    setPendingVoteId(req.id);
    try {
      const res = await api.voteSongRequest(req.id, direction, confirmDelete);
      if (res.request_deleted) {
        setNotice(t('requestRemoved'));
      } else if (direction === 'up' && res.your_vote === 1) {
        setThanks('voted');
      }
      await refresh();
    } catch (e) {
      if (errorCode(e) === 'would_delete_request') {
        setConfirm({ kind: 'delete', req, direction });
      } else {
        setError(errorMessage(e));
      }
    } finally {
      setPendingVoteId(null);
    }
  };

  const handleVote = (req: SongRequestPublic, direction: 'up' | 'down') => {
    if (!isSignedIn) return;
    const mine = direction === 'up' ? 1 : -1;
    if (req.your_vote === mine) {
      // Undo: frees today's vote (the server asks to confirm if that deletes the request).
      void castVote(req, direction);
      return;
    }
    setConfirm({ kind: 'spend', req, direction, moving: !!board?.voted_today });
  };

  const handleBuyCredits = () => {
    // Same hand-off as "make it yourself", but straight to the buy-credits dialog.
    if (window.location.hostname === REQUESTS_HOST) {
      window.location.href = 'https://gen.nomadkaraoke.com/app?buy=1';
      return;
    }
    router.push('/app?buy=1');
  };

  const handleMakeItYourself = async () => {
    // Board sign-in grants no credit; claim the standard welcome credit (idempotent)
    // then send them into the generator. Signed-out users go to /app and sign up
    // normally (which grants the credit the usual way).
    if (isSignedIn) {
      try {
        await api.claimWelcomeCredit();
      } catch {
        /* non-blocking — the create flow still works, credit can be claimed later */
      }
    }
    // requests.nomadkaraoke.com serves only the board (edge rules send every other path
    // to gen), so leave the vanity host instead of client-routing the app onto it.
    if (window.location.hostname === REQUESTS_HOST) {
      window.location.href = 'https://gen.nomadkaraoke.com/app';
      return;
    }
    router.push('/app');
  };

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <div className="max-w-2xl mx-auto px-4 py-8 space-y-8">
        {/* Header */}
        <header className="relative text-center space-y-3">
          <div className="absolute end-0 top-0">
            <LanguageSwitcher onSelectLocale={onRequestsHost ? selectLocaleOnRequestsHost : undefined} />
          </div>
          <div className="flex justify-center">
            <div className="p-3 rounded-full" style={{ backgroundColor: 'rgba(168, 85, 247, 0.1)' }}>
              <Music className="w-8 h-8" style={{ color: 'var(--accent)' }} />
            </div>
          </div>
          <h1 className="text-3xl font-bold">{t('title')}</h1>
          <p className="text-base" style={{ color: 'var(--text-muted)' }}>{t('subtitle')}</p>
        </header>

        {/* Deep-linked request (e.g. from a YouTube comment reply) */}
        {focusReq && (
          <p
            data-testid="deep-link-banner"
            className="text-sm rounded-xl px-4 py-3 text-center"
            style={{ backgroundColor: 'rgba(168, 85, 247, 0.08)', color: 'var(--accent)' }}
          >
            {deepLinkVoted || (isSignedIn && focusReq.your_vote === 1)
              ? t('deepLinkUpvoted', { title: focusReq.title, artist: focusReq.artist })
              : isSignedIn
                ? t('deepLinkSignedIn', { title: focusReq.title, artist: focusReq.artist })
                : t('deepLinkSignIn', { title: focusReq.title, artist: focusReq.artist })}
          </p>
        )}

        {/* Submit / sign-in card */}
        <section
          className="rounded-2xl p-6 space-y-4"
          style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}
        >
          <h2 className="font-semibold text-lg">{t('submitHeading')}</h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('dailyLimitsNote')}</p>

          {isSignedIn ? (
            <form onSubmit={handleSubmit} className="space-y-3">
              {board?.voted_today && (
                <p data-testid="vote-spent-note" className="text-sm rounded-lg px-3 py-2" style={{ backgroundColor: 'rgba(168, 85, 247, 0.08)', color: 'var(--accent)' }}>
                  {t('voteSpentCannotSubmit')}
                </p>
              )}
              <input
                type="text"
                disabled={!!board?.voted_today}
                value={artist}
                onChange={(e) => setArtist(e.target.value)}
                placeholder={t('artistPlaceholder')}
                className="w-full px-4 py-3 rounded-xl text-base"
                style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)', border: '1px solid var(--card-border)' }}
              />
              <input
                type="text"
                disabled={!!board?.voted_today}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('titlePlaceholder')}
                className="w-full px-4 py-3 rounded-xl text-base"
                style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)', border: '1px solid var(--card-border)' }}
              />
              <button
                type="submit"
                disabled={submitting || !!board?.voted_today || !artist.trim() || !title.trim()}
                className="w-full py-3 px-6 rounded-xl font-semibold flex items-center justify-center gap-2 transition-all hover:scale-[1.01] active:scale-[0.99] disabled:opacity-50 disabled:hover:scale-100"
                style={{ backgroundColor: 'var(--accent)', color: 'white' }}
              >
                {submitting ? (<><Loader2 className="w-5 h-5 animate-spin" />{t('submitting')}</>) : t('submitButton')}
              </button>
              {communityData && !communityDismissed && (
                <CommunityVersionBanner
                  data={communityData}
                  subtitle={t('communityExistsNote')}
                  onDismiss={() => setCommunityDismissed(true)}
                />
              )}
            </form>
          ) : linkSent ? (
            <div className="flex items-center gap-2" style={{ color: 'var(--accent)' }}>
              <CheckCircle className="w-5 h-5" />
              <p className="font-medium">{t('linkSent', { email })}</p>
            </div>
          ) : (
            <form onSubmit={handleSendLink} className="space-y-3">
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('signInToSubmit')}</p>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5" style={{ color: 'var(--text-muted)' }} />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t('emailPlaceholder')}
                  className="w-full pl-11 pr-4 py-3 rounded-xl text-base"
                  style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)', border: '1px solid var(--card-border)' }}
                />
              </div>
              <button
                type="submit"
                disabled={sendingLink}
                className="w-full py-3 px-6 rounded-xl font-semibold flex items-center justify-center gap-2 disabled:opacity-50"
                style={{ backgroundColor: 'var(--accent)', color: 'white' }}
              >
                {sendingLink ? (<><Loader2 className="w-5 h-5 animate-spin" />{t('sending')}</>) : t('sendLink')}
              </button>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('noPasswordNeeded')}</p>
            </form>
          )}

          {correctionNote && (
            <p className="text-sm rounded-lg px-3 py-2" style={{ backgroundColor: 'rgba(168, 85, 247, 0.08)', color: 'var(--accent)' }}>
              {correctionNote}
            </p>
          )}
          {error && <p className="text-sm text-red-400">{error}</p>}
          {notice && <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{notice}</p>}
        </section>

        {/* Thank-you + gentle nudge towards making tracks directly with credits */}
        {thanks && (
          <section
            data-testid="thanks-panel"
            className="rounded-2xl p-6 text-center space-y-3"
            style={{ backgroundColor: 'rgba(168, 85, 247, 0.08)', border: '1px solid var(--accent)' }}
          >
            <div className="flex justify-center">
              <CheckCircle className="w-6 h-6" style={{ color: 'var(--accent)' }} />
            </div>
            <h2 className="font-semibold text-lg">
              {thanks === 'submitted' ? t('thanksSubmittedHeading') : t('thanksVotedHeading')}
            </h2>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('thanksBody')}</p>
            <div className="flex flex-col sm:flex-row gap-2 justify-center">
              <button
                type="button"
                onClick={handleBuyCredits}
                className="inline-flex items-center justify-center gap-2 py-3 px-6 rounded-xl font-semibold"
                style={{ backgroundColor: 'var(--accent)', color: 'white' }}
              >
                {t('thanksBuyButton')}
                <ArrowRight className="w-5 h-5" />
              </button>
              <button
                type="button"
                onClick={() => setThanks(null)}
                className="py-3 px-6 rounded-xl font-medium"
                style={{ color: 'var(--text-muted)' }}
              >
                {t('thanksDismiss')}
              </button>
            </div>
          </section>
        )}

        {/* Daily-vote hint */}
        {isSignedIn && board?.voted_today && (
          <p className="text-center text-sm" style={{ color: 'var(--text-muted)' }}>{t('votedTodayNote')}</p>
        )}

        {/* Board list */}
        <section className="space-y-3">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--accent)' }} />
            </div>
          ) : board && board.requests.length > 0 ? (
            board.requests.map((req, idx) => (
              <RequestRow
                key={req.id}
                rank={idx + 1}
                req={req}
                highlighted={req.id === focusId}
                canVote={isSignedIn}
                pending={pendingVoteId === req.id}
                onVote={handleVote}
                votesLabel={t('votes', { count: req.vote_count })}
                beingMadeLabel={t('statusBeingMade')}
              />
            ))
          ) : (
            <p className="text-center py-8" style={{ color: 'var(--text-muted)' }}>{t('noRequests')}</p>
          )}
        </section>

        {/* Convert-to-gen CTA */}
        <section
          className="rounded-2xl p-6 text-center space-y-3"
          style={{ backgroundColor: 'rgba(168, 85, 247, 0.08)', border: '1px solid var(--card-border)' }}
        >
          <div className="flex justify-center">
            <Sparkles className="w-6 h-6" style={{ color: 'var(--accent)' }} />
          </div>
          <h2 className="font-semibold text-lg">{t('convertHeading')}</h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('convertBody')}</p>
          <button
            onClick={handleMakeItYourself}
            className="inline-flex items-center gap-2 py-3 px-6 rounded-xl font-semibold transition-all hover:scale-[1.02]"
            style={{ backgroundColor: 'var(--accent)', color: 'white' }}
          >
            {t('convertButton')}
            <ArrowRight className="w-5 h-5" />
          </button>
        </section>

        {/* Recently made */}
        {board && board.published.length > 0 && (
          <section className="space-y-3">
            <h2 className="font-semibold text-lg">{t('publishedHeading')}</h2>
            {board.published.map((req) => (
              <div
                key={req.id}
                className="flex items-center justify-between rounded-xl px-4 py-3"
                style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}
              >
                <div className="min-w-0">
                  <p className="font-medium truncate">{req.title}</p>
                  <p className="text-sm truncate" style={{ color: 'var(--text-muted)' }}>{req.artist}</p>
                </div>
                {req.youtube_url && (
                  <a
                    href={req.youtube_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-sm font-medium shrink-0 ml-3"
                    style={{ color: 'var(--accent)' }}
                  >
                    <Youtube className="w-4 h-4" />
                    {t('watchOnYoutube')}
                  </a>
                )}
              </div>
            ))}
          </section>
        )}
      </div>

      {confirm && (
        <ConfirmDialog
          title={
            confirm.kind === 'delete'
              ? t('deleteTitle')
              : confirm.kind === 'submit'
                ? t('submitConfirmTitle')
                : confirm.moving
                  ? t('moveVoteTitle')
                  : t('spendVoteTitle')
          }
          body={
            confirm.kind === 'delete'
              ? t('deleteBody', { title: confirm.req.title, artist: confirm.req.artist })
              : confirm.kind === 'submit'
                ? t('submitConfirmBody', { artist: artist.trim(), title: title.trim() })
                : confirm.moving
                  ? t('moveVoteBody', { title: confirm.req.title, artist: confirm.req.artist })
                  : t('spendVoteBody', { title: confirm.req.title, artist: confirm.req.artist })
          }
          confirmLabel={confirm.kind === 'delete' ? t('deleteConfirm') : t('confirm')}
          cancelLabel={t('cancel')}
          destructive={confirm.kind === 'delete'}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            if (confirm.kind === 'submit') void doSubmit();
            else void castVote(confirm.req, confirm.direction, confirm.kind === 'delete');
          }}
        />
      )}
    </div>
  );
}

function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  destructive,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.6)' }}
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-w-md w-full rounded-2xl p-6 space-y-4"
        style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)', color: 'var(--text)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-semibold text-lg">{title}</h2>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{body}</p>
        <div className="flex gap-2 justify-end">
          <button type="button" onClick={onCancel} className="py-2 px-4 rounded-xl font-medium" style={{ color: 'var(--text-muted)' }}>
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="py-2 px-4 rounded-xl font-semibold"
            style={{ backgroundColor: destructive ? '#dc2626' : 'var(--accent)', color: 'white' }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function RequestRow({
  rank,
  req,
  highlighted,
  canVote,
  pending,
  onVote,
  votesLabel,
  beingMadeLabel,
}: {
  rank: number;
  req: SongRequestPublic;
  highlighted?: boolean;
  canVote: boolean;
  pending: boolean;
  onVote: (req: SongRequestPublic, direction: 'up' | 'down') => void;
  votesLabel: string;
  beingMadeLabel: string;
}) {
  const up = req.your_vote === 1;
  const down = req.your_vote === -1;
  return (
    <div
      id={`request-${req.id}`}
      data-highlighted={highlighted ? 'true' : undefined}
      className="flex items-center gap-3 rounded-xl px-4 py-3"
      style={{
        backgroundColor: 'var(--card)',
        border: highlighted ? '2px solid var(--accent)' : '1px solid var(--card-border)',
      }}
    >
      {/* Vote controls */}
      <div className="flex flex-col items-center w-10 shrink-0">
        <button
          type="button"
          aria-label="upvote"
          disabled={!canVote || pending}
          onClick={() => onVote(req, 'up')}
          className="p-1 rounded disabled:opacity-40"
          style={{ color: up ? 'var(--accent)' : 'var(--text-muted)' }}
        >
          <ChevronUp className="w-5 h-5" />
        </button>
        <span className="text-sm font-semibold tabular-nums">{req.vote_count}</span>
        <button
          type="button"
          aria-label="downvote"
          disabled={!canVote || pending}
          onClick={() => onVote(req, 'down')}
          className="p-1 rounded disabled:opacity-40"
          style={{ color: down ? 'var(--accent)' : 'var(--text-muted)' }}
        >
          <ChevronDown className="w-5 h-5" />
        </button>
      </div>

      {/* Rank + song */}
      <span className="text-sm w-6 text-right tabular-nums shrink-0" style={{ color: 'var(--text-muted)' }}>{rank}</span>
      <div className="min-w-0 flex-1">
        <p className="font-medium truncate">{req.title}</p>
        <p className="text-sm truncate" style={{ color: 'var(--text-muted)' }}>{req.artist}</p>
      </div>

      {/* Status / votes */}
      <div className="text-right shrink-0">
        {req.status === 'in_progress' || req.status === 'queued' ? (
          <span className="text-xs font-medium px-2 py-1 rounded-full" style={{ backgroundColor: 'rgba(168, 85, 247, 0.12)', color: 'var(--accent)' }}>
            {beingMadeLabel}
          </span>
        ) : (
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{votesLabel}</span>
        )}
      </div>
    </div>
  );
}
