/**
 * Tenant track codes (e.g. Vocal Star's "VS6588") prefix the output folder and
 * every delivered file, so they're kept to a short filesystem-safe token.
 * Mirrors backend `normalize_track_code` in backend/api/routes/file_upload.py.
 */
const TRACK_CODE_RE = /^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$/
export const TRACK_CODE_MAX_LEN = 24

/** True when blank (the code is optional) or a valid code. */
export function isValidTrackCode(raw: string): boolean {
  const code = raw.trim()
  return !code || (code.length <= TRACK_CODE_MAX_LEN && TRACK_CODE_RE.test(code))
}

/** Cleaned code to send, or undefined when blank. */
export function normalizeTrackCode(raw: string): string | undefined {
  const code = raw.trim()
  return code ? code.toUpperCase() : undefined
}
