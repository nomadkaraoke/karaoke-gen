/**
 * Client-side IP geolocation cache and fetch utilities.
 *
 * Caches results in memory for the browser session to avoid
 * redundant API calls when the same IP appears multiple times.
 */

import { adminApi } from "./api"
import type { IpGeoInfo } from "./api"

// In-memory cache for the current session
const cache = new Map<string, IpGeoInfo>()

// Pending fetches to deduplicate concurrent requests for the same IP
const pending = new Map<string, Promise<IpGeoInfo>>()

export async function fetchIpInfo(ip: string): Promise<IpGeoInfo> {
  if (!ip) return { status: "private", ip: "" }

  const cached = cache.get(ip)
  if (cached) return cached

  // Deduplicate concurrent requests
  const existing = pending.get(ip)
  if (existing) return existing

  const promise = adminApi.getIpInfo(ip).then((result) => {
    cache.set(ip, result)
    pending.delete(ip)
    return result
  }).catch(() => {
    pending.delete(ip)
    return { status: "error", ip } as IpGeoInfo
  })

  pending.set(ip, promise)
  return promise
}

export async function fetchIpInfoBatch(ips: string[]): Promise<Record<string, IpGeoInfo>> {
  const unique = [...new Set(ips.filter(Boolean))]

  // Split into cached and uncached
  const results: Record<string, IpGeoInfo> = {}
  const uncached: string[] = []

  for (const ip of unique) {
    const cached = cache.get(ip)
    if (cached) {
      results[ip] = cached
    } else {
      uncached.push(ip)
    }
  }

  if (uncached.length === 0) return results

  try {
    const batchResults = await adminApi.getIpInfoBatch(uncached)
    for (const [ip, info] of Object.entries(batchResults)) {
      cache.set(ip, info)
      results[ip] = info
    }
  } catch {
    // Mark all as error
    for (const ip of uncached) {
      results[ip] = { status: "error", ip }
    }
  }

  return results
}

/**
 * Convert a 2-letter country code to a flag emoji.
 * e.g., "US" -> "🇺🇸", "VN" -> "🇻🇳"
 */
export function countryCodeToFlag(code: string): string {
  if (!code || code.length !== 2) return ""
  const offset = 0x1F1E6 - 65 // 'A' = 65
  return String.fromCodePoint(
    code.charCodeAt(0) + offset,
    code.charCodeAt(1) + offset
  )
}
