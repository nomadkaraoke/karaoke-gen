import type { CorrectionData } from '../types'
import type {
  ReviewSession,
  ReviewSessionSummary,
  ReviewSessionWithData,
} from '@/lib/api'

/**
 * A localStorage-backed implementation of the review-session API, so the normal
 * Session Restore dialog works identically when running karaoke-gen locally
 * (no server) — replacing the old ad-hoc `window.confirm` crash-recovery path.
 *
 * One rolling session is kept per song/job (single-user local case): each save
 * upserts it. This mirrors the subset of `LyricsReviewApiClient` the review UI
 * uses for sessions.
 */

const STORAGE_KEY = 'lyrics_review_local_sessions'

type Store = Record<string, ReviewSessionWithData>

function readStore(): Store {
  if (typeof window === 'undefined') return {}
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Store) : {}
  } catch {
    return {}
  }
}

function writeStore(store: Store): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    // Best-effort: localStorage is a convenience, never break the UI on quota.
  }
}

export interface LocalSessionStoreOptions {
  /** Stable key for this song/job (job id, falling back to a song hash). */
  storeKey: string
  jobId: string
  artist?: string | null
  title?: string | null
}

export interface LocalSessionStore {
  saveReviewSession: (
    data: CorrectionData,
    editCount: number,
    trigger: string,
    summary: ReviewSessionSummary,
  ) => Promise<{ status: string; session_id?: string }>
  listReviewSessions: () => Promise<{ sessions: ReviewSession[] }>
  getReviewSession: (sessionId: string) => Promise<ReviewSessionWithData>
  deleteReviewSession: (sessionId: string) => Promise<{ status: string }>
}

export function createLocalSessionStore({
  storeKey,
  jobId,
  artist = null,
  title = null,
}: LocalSessionStoreOptions): LocalSessionStore {
  return {
    async saveReviewSession(data, editCount, trigger, summary) {
      const store = readStore()
      const now = new Date().toISOString()
      const existing = store[storeKey]
      const session: ReviewSessionWithData = {
        session_id: existing?.session_id ?? `local-${storeKey}`,
        job_id: jobId,
        user_email: 'local',
        created_at: existing?.created_at ?? now,
        updated_at: now,
        edit_count: editCount,
        trigger: (trigger as ReviewSession['trigger']) ?? 'auto',
        audio_duration_seconds: null,
        artist,
        title,
        summary,
        correction_data: data,
      }
      store[storeKey] = session
      writeStore(store)
      return { status: 'saved', session_id: session.session_id }
    },

    async listReviewSessions() {
      const session = readStore()[storeKey]
      if (!session) return { sessions: [] }
      // Parity with the server list endpoint: metadata only, no correction_data.
      const { correction_data: _data, ...meta } = session
      return { sessions: [meta] }
    },

    async getReviewSession(sessionId) {
      const session = readStore()[storeKey]
      if (!session || session.session_id !== sessionId) {
        throw new Error('Local review session not found')
      }
      return session
    },

    async deleteReviewSession(sessionId) {
      const store = readStore()
      if (store[storeKey]?.session_id === sessionId) {
        delete store[storeKey]
        writeStore(store)
      }
      return { status: 'deleted' }
    },
  }
}
