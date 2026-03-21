/**
 * Device fingerprinting for anti-abuse rate limiting.
 *
 * Uses FingerprintJS (open source) to generate a browser fingerprint.
 * The fingerprint is sent with magic link requests to detect
 * same-browser multi-account abuse.
 *
 * Gracefully degrades: if fingerprinting fails, returns null
 * and the backend falls back to IP-only rate limiting.
 */

let cachedFingerprint: string | null = null

export async function getDeviceFingerprint(): Promise<string | null> {
  if (cachedFingerprint) return cachedFingerprint

  try {
    const FingerprintJS = await import("@fingerprintjs/fingerprintjs")
    const fp = await FingerprintJS.load()
    const result = await fp.get()
    cachedFingerprint = result.visitorId
    return cachedFingerprint
  } catch {
    // Fingerprinting may fail due to browser extensions, privacy settings, etc.
    // Fall back to no fingerprint — backend uses IP-only rate limiting.
    return null
  }
}
