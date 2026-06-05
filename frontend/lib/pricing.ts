// MIRRORS backend/services/pricing.py — keep these constants in sync.
export const SECONDS_PER_CREDIT_TIER = 600;
export const DURATION_CREDIT_BLOCK_SECONDS = 3600;

export function durationToCredits(seconds: number): number {
  if (seconds == null || seconds < 0) return 1;
  return Math.max(1, Math.ceil(seconds / SECONDS_PER_CREDIT_TIER));
}

export function isBlocked(seconds: number): boolean {
  return seconds != null && seconds > DURATION_CREDIT_BLOCK_SECONDS;
}

export function formatDurationCost(seconds: number): { minutes: number; credits: number } {
  return { minutes: Math.ceil(seconds / 60), credits: durationToCredits(seconds) };
}
