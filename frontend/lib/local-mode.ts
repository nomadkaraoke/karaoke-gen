/**
 * Local mode detection for karaoke-gen CLI
 *
 * When running the local CLI (karaoke-gen), the frontend is served from
 * a local server on localhost. This module detects local mode and provides
 * utilities for adapting the frontend behavior accordingly.
 *
 * In local mode:
 * - Authentication is skipped (no user login required)
 * - Job ID is always "local" (no real jobs in database)
 * - API calls go to localhost
 */

// Ports used by local review servers
// Port 8000-8001 are used by lyrics review server (8001 for side-by-side testing)
const LOCAL_LYRICS_REVIEW_PORTS = ['8000', '8001', '8764', '8765', '8766', '8767', '8768', '8769']
// Ports 8765+ are used by instrumental review server
const LOCAL_INSTRUMENTAL_REVIEW_PORTS = ['8765', '8766', '8767', '8768', '8769', '8770']
// Development/testing ports (Next.js dev server)
const DEV_TESTING_PORTS = ['3000', '3001', '3002']
const ALL_LOCAL_PORTS = [...new Set([...LOCAL_LYRICS_REVIEW_PORTS, ...LOCAL_INSTRUMENTAL_REVIEW_PORTS, ...DEV_TESTING_PORTS])]

/**
 * Check if the frontend is running in local CLI mode
 */
export function isLocalMode(): boolean {
  if (typeof window === 'undefined') return false

  const { hostname, port } = window.location

  // Must be on localhost
  if (hostname !== 'localhost' && hostname !== '127.0.0.1') return false

  // Must be on one of the known local server ports
  return ALL_LOCAL_PORTS.includes(port)
}

/**
 * Get the local job ID (always "local" in local mode)
 */
export function getLocalJobId(): string {
  return 'local'
}

/**
 * Check if running in local lyrics review mode
 */
export function isLocalLyricsReview(): boolean {
  if (!isLocalMode()) return false
  // Could check specific port or URL path in the future
  return true
}

/**
 * Check if running in local instrumental review mode
 */
export function isLocalInstrumentalReview(): boolean {
  if (!isLocalMode()) return false
  // Could check specific port or URL path in the future
  return true
}

/**
 * Mock user object for local mode (no auth required)
 */
export const LOCAL_MODE_USER = {
  email: 'local@localhost',
  role: 'admin' as const,
  credits: 999,
  display_name: 'Local User',
  total_jobs_created: 0,
  total_jobs_completed: 0,
}

/**
 * Mock job object for local mode
 */
export function createLocalModeJob(options: {
  routeType: 'review' | 'instrumental'
  artist?: string
  title?: string
}) {
  const status = options.routeType === 'review'
    ? 'awaiting_review'
    : 'awaiting_instrumental_selection'

  return {
    job_id: 'local',
    status,
    progress: 50,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    artist: options.artist || 'Local Artist',
    title: options.title || 'Local Title',
    user_email: 'local@localhost',
    audio_hash: 'local',
  }
}
