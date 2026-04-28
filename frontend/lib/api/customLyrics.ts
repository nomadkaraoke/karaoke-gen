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

export interface CustomLyricsParams {
  existingLines: string[]
  customText?: string
  notes?: string
  artist?: string
  title?: string
  file?: File
}

export interface CustomLyricsResponse {
  lines: string[]
  warnings: string[]
  model: string
  lineCountMismatch: boolean
  retryCount: number
  durationMs: number
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
    warnings: data.warnings ?? [],
    model: data.model,
    lineCountMismatch: Boolean(data.line_count_mismatch),
    retryCount: Number(data.retry_count ?? 0),
    durationMs: Number(data.duration_ms ?? 0),
  }
}
