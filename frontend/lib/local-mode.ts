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
// Port 8000-8001 are used by local review server (8001 for side-by-side testing)
const LOCAL_REVIEW_PORTS = ['8000', '8001', '8764', '8765', '8766', '8767', '8768', '8769', '8770']
// Development/testing ports (Next.js dev server)
const DEV_TESTING_PORTS = ['3000', '3001', '3002']
const ALL_LOCAL_PORTS = [...new Set([...LOCAL_REVIEW_PORTS, ...DEV_TESTING_PORTS])]

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
 * Check if running in local review mode
 */
export function isLocalReview(): boolean {
  return isLocalMode()
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
  return {
    job_id: 'local',
    status: 'awaiting_review',
    progress: 50,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    artist: options.artist || 'Local Artist',
    title: options.title || 'Local Title',
    user_email: 'local@localhost',
    audio_hash: 'local',
  }
}
