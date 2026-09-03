'use client';

import { useCallback, useEffect, useState } from 'react';
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
import { useAuth } from '@/lib/auth';
import type { BoardResponse, SongRequestPublic } from '@/lib/types';

const BOARD_PURPOSE = 'requests_board';

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

  // Sign-in
  const [email, setEmail] = useState('');
  const [sendingLink, setSendingLink] = useState(false);
  const [linkSent, setLinkSent] = useState(false);

  const [pendingVoteId, setPendingVoteId] = useState<string | null>(null);

  const isSignedIn = !!user;

  const refresh = useCallback(async () => {
    try {
      const data = await api.getRequestsBoard();
      setBoard(data);
    } catch {
      setError(t('errorGeneric'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    // Hydrate the user if we have a stored session token but no user yet.
    if (!user && getAccessToken()) {
      fetchUser().catch(() => {});
    }
    refresh();
  }, [refresh, fetchUser, user]);

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
      const ok = await sendMagicLink(trimmed, BOARD_PURPOSE);
      setLinkSent(ok);
      if (!ok) setError(t('errorGeneric'));
    } catch {
      setError(t('errorGeneric'));
    } finally {
      setSendingLink(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setCorrectionNote('');
    if (!artist.trim() || !title.trim()) return;
    setSubmitting(true);
    try {
      const res = await api.submitSongRequest(artist.trim(), title.trim());
      if (res.was_corrected) {
        setCorrectionNote(t('correctedNote', { artist: res.canonical_artist, title: res.canonical_title }));
      }
      setArtist('');
      setTitle('');
      await refresh();
    } catch {
      setError(t('errorGeneric'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleVote = async (req: SongRequestPublic, direction: 'up' | 'down') => {
    if (!isSignedIn) return;
    setError('');
    setPendingVoteId(req.id);
    try {
      await api.voteSongRequest(req.id, direction);
      await refresh();
    } catch {
      setError(t('errorGeneric'));
    } finally {
      setPendingVoteId(null);
    }
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
    router.push('/app');
  };

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <div className="max-w-2xl mx-auto px-4 py-8 space-y-8">
        {/* Header */}
        <header className="text-center space-y-3">
          <div className="flex justify-center">
            <div className="p-3 rounded-full" style={{ backgroundColor: 'rgba(168, 85, 247, 0.1)' }}>
              <Music className="w-8 h-8" style={{ color: 'var(--accent)' }} />
            </div>
          </div>
          <h1 className="text-3xl font-bold">{t('title')}</h1>
          <p className="text-base" style={{ color: 'var(--text-muted)' }}>{t('subtitle')}</p>
        </header>

        {/* Submit / sign-in card */}
        <section
          className="rounded-2xl p-6 space-y-4"
          style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}
        >
          <h2 className="font-semibold text-lg">{t('submitHeading')}</h2>

          {isSignedIn ? (
            <form onSubmit={handleSubmit} className="space-y-3">
              <input
                type="text"
                value={artist}
                onChange={(e) => setArtist(e.target.value)}
                placeholder={t('artistPlaceholder')}
                className="w-full px-4 py-3 rounded-xl text-base"
                style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)', border: '1px solid var(--card-border)' }}
              />
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('titlePlaceholder')}
                className="w-full px-4 py-3 rounded-xl text-base"
                style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)', border: '1px solid var(--card-border)' }}
              />
              <button
                type="submit"
                disabled={submitting || !artist.trim() || !title.trim()}
                className="w-full py-3 px-6 rounded-xl font-semibold flex items-center justify-center gap-2 transition-all hover:scale-[1.01] active:scale-[0.99] disabled:opacity-50 disabled:hover:scale-100"
                style={{ backgroundColor: 'var(--accent)', color: 'white' }}
              >
                {submitting ? (<><Loader2 className="w-5 h-5 animate-spin" />{t('submitting')}</>) : t('submitButton')}
              </button>
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
        </section>

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
    </div>
  );
}

function RequestRow({
  rank,
  req,
  canVote,
  pending,
  onVote,
  votesLabel,
  beingMadeLabel,
}: {
  rank: number;
  req: SongRequestPublic;
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
      className="flex items-center gap-3 rounded-xl px-4 py-3"
      style={{ backgroundColor: 'var(--card)', border: '1px solid var(--card-border)' }}
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
