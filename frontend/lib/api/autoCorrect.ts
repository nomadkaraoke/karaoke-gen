/**
 * Client for the AI auto-correct suggestions endpoint.
 *
 * POST /api/review/{job_id}/auto-correct
 *
 * Stateless: posts the client's current working segments + reference lyrics
 * and returns word-id-keyed suggestions. Nothing is applied server-side —
 * the reviewer accepts/rejects each suggestion in the UI and persistence
 * flows through the existing corrections/complete paths.
 */
import type { LyricsSegment, ReferenceSource } from '@/lib/lyrics-review/types'

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'https://api.nomadkaraoke.com'

export type AiSuggestionOp = 'replace' | 'delete' | 'insert_after'

export type AiSuggestionCategory =
  | 'mishearing'
  | 'grammar'
  | 'adlib_removal'
  | 'repeated_chorus_fix'
  | 'formatting'
  | 'other'

export interface AutoCorrectSettings {
  suggest_adlib_removal: boolean
  allow_insertions: boolean
  min_confidence: number
  /** Query all configured models in parallel and report consensus. */
  compare_models: boolean
}

export const DEFAULT_AUTO_CORRECT_SETTINGS: AutoCorrectSettings = {
  suggest_adlib_removal: true,
  allow_insertions: true,
  min_confidence: 0,
  compare_models: false,
}

export interface AiSuggestion {
  id: string
  op: AiSuggestionOp
  word_ids: string[]
  segment_ids: string[]
  original_text: string
  new_text: string
  reason: string
  category: AiSuggestionCategory
  confidence: number
  /** Models that proposed this exact suggestion. */
  models: string[]
  /** How many of total_models agreed on this exact suggestion. */
  consensus: number
  total_models: number
  /** Suggestions sharing a conflict_group target overlapping words with
   *  different outcomes — accept at most one per group. */
  conflict_group: string | null
}

export interface AutoCorrectResponse {
  suggestions: AiSuggestion[]
  model: string
  elapsed_seconds: number
  settings_applied: AutoCorrectSettings
  warnings: string[]
}

export class AutoCorrectApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'AutoCorrectApiError'
    this.status = status
  }
}

export interface AutoCorrectParams {
  segments: LyricsSegment[]
  referenceLyrics: Record<string, ReferenceSource>
  artist?: string
  title?: string
  settings: AutoCorrectSettings
}

export async function fetchAutoCorrectSuggestions(
  jobId: string,
  params: AutoCorrectParams,
  signal?: AbortSignal,
  token?: string,
): Promise<AutoCorrectResponse> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(
    `${API_BASE}/api/review/${encodeURIComponent(jobId)}/auto-correct`,
    {
      method: 'POST',
      headers,
      signal,
      body: JSON.stringify({
        segments: params.segments,
        reference_lyrics: params.referenceLyrics,
        artist: params.artist,
        title: params.title,
        settings: params.settings,
      }),
    },
  )

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* swallow JSON parse errors and use generic message */
    }
    throw new AutoCorrectApiError(response.status, detail)
  }

  const data = await response.json()
  return {
    suggestions: (data.suggestions ?? []).map((s: AiSuggestion) => ({
      ...s,
      models: s.models ?? [],
      consensus: s.consensus ?? 1,
      total_models: s.total_models ?? 1,
      conflict_group: s.conflict_group ?? null,
    })),
    model: data.model,
    elapsed_seconds: data.elapsed_seconds,
    settings_applied: data.settings_applied,
    warnings: data.warnings ?? [],
  }
}
