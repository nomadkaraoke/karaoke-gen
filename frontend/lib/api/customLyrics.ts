/**
 * Client for the LLM-powered custom lyrics endpoint.
 *
 * POST /api/review/{job_id}/custom-lyrics/generate
 *
 * The endpoint is stateless — it returns generated lines for the editable
 * preview. Persistence still flows through the existing replace-segments
 * Save path.
 */
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'https://api.nomadkaraoke.com'

export type StrictnessLevel = 'verbatim' | 'loose' | 'balanced' | 'tight' | 'strict'
export type StopReason =
  | 'success'
  | 'plateau'
  | 'max_iters_reached'
  | 'line_count_mismatch'
  | 'verbatim_skip'
export type LineSeverity = 'ok' | 'minor' | 'major'

export interface GenerationSettings {
  allow_reword: boolean
  allow_omit: boolean
  fixed_line_count: boolean
  strictness: StrictnessLevel
}

export const DEFAULT_GENERATION_SETTINGS: GenerationSettings = {
  allow_reword: true,
  allow_omit: true,
  fixed_line_count: true,
  strictness: 'balanced',
}

export interface LineMetadata {
  line_index: number
  target_text: string
  candidate_text: string
  target_syllables: number[]
  candidate_syllables: number[]
  min_delta: number
  passes: boolean
  severity: LineSeverity
  time_budget_seconds: number
}

export interface CustomLyricsParams {
  existingLines: string[]
  customText?: string
  notes?: string
  artist?: string
  title?: string
  file?: File
  settings: GenerationSettings
}

export interface CustomLyricsResponse {
  lines: string[]
  line_metadata: LineMetadata[]
  iterations_used: number
  stop_reason: StopReason
  settings_applied: GenerationSettings
  new_segment_timing: Array<[number, number]> | null
  line_count_mismatch: boolean
  warnings: string[]
  model: string
}

export class CustomLyricsApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'CustomLyricsApiError'
    this.status = status
  }
}

export async function generateCustomLyrics(
  jobId: string,
  params: CustomLyricsParams,
  signal?: AbortSignal,
  token?: string,
): Promise<CustomLyricsResponse> {
  const form = new FormData()
  form.set('existing_lines', JSON.stringify(params.existingLines))
  if (params.customText) form.set('custom_text', params.customText)
  if (params.notes) form.set('notes', params.notes)
  if (params.artist) form.set('artist', params.artist)
  if (params.title) form.set('title', params.title)
  if (params.file) form.set('file', params.file)
  form.set('settings_json', JSON.stringify(params.settings))

  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(
    `${API_BASE}/api/review/${encodeURIComponent(jobId)}/custom-lyrics/generate`,
    { method: 'POST', body: form, headers, signal },
  )

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* swallow JSON parse errors and use generic message */
    }
    throw new CustomLyricsApiError(response.status, detail)
  }

  const data = await response.json()
  return {
    lines: data.lines,
    line_metadata: data.line_metadata,
    iterations_used: data.iterations_used,
    stop_reason: data.stop_reason,
    settings_applied: data.settings_applied,
    new_segment_timing: data.new_segment_timing ?? null,
    line_count_mismatch: data.line_count_mismatch,
    warnings: data.warnings ?? [],
    model: data.model,
  }
}
