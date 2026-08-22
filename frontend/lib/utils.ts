import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Parse a timestamp returned by the backend into a Date.
 *
 * Backend timestamps (job.created_at, log timestamps, etc.) are UTC but are
 * frequently serialized from naive `datetime.utcnow()` values, producing ISO
 * strings with NO timezone offset (e.g. "2026-08-14T18:10:25.123456"). The
 * JS `Date` constructor parses such offset-less date-time strings as *local*
 * time, which displays the raw UTC wall-clock unconverted (off by the viewer's
 * UTC offset). To fix this we treat offset-less timestamps as UTC by appending
 * "Z". Strings that already carry an offset ("Z" or "+HH:MM"/"-HH:MM") — or that
 * aren't the expected ISO date-time shape — are passed through unchanged.
 */
export function parseServerDate(value: string | number | Date): Date {
  if (value instanceof Date) return value
  if (typeof value === 'number') return new Date(value)

  // ISO date-time with no timezone designator → assume UTC.
  // Matches "YYYY-MM-DDTHH:MM:SS" with optional fractional seconds and no
  // trailing Z / ±HH:MM offset.
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(value)) {
    return new Date(`${value}Z`)
  }
  return new Date(value)
}
