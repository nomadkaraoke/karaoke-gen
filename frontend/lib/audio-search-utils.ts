import { AudioSearchResult } from "@/lib/api"

// Extended result type to include all backend fields
export interface ExtendedAudioSearchResult extends AudioSearchResult {
  channel?: string
  view_count?: number
  formatted_duration?: string
  formatted_size?: string
  release_type?: string
  label?: string
  edition_info?: string
  target_file?: string
  quality_data?: {
    format?: string
    bit_depth?: number
    sample_rate?: number
    bitrate?: number
    media?: string
  }
}

// Category configuration matching flacfetch
export interface CategoryConfig {
  name: string
  maxDisplay: number
  color: string
  borderColor: string
}

export const CATEGORY_CONFIG: Record<string, CategoryConfig> = {
  'BEST CHOICE': { name: 'BEST CHOICE', maxDisplay: 3, color: 'text-amber-400', borderColor: 'border-amber-500/30' },
  'HI-RES 24-BIT': { name: 'HI-RES 24-BIT', maxDisplay: 3, color: 'text-purple-400', borderColor: 'border-purple-500/30' },
  'STUDIO ALBUMS': { name: 'STUDIO ALBUMS', maxDisplay: 3, color: 'text-blue-400', borderColor: 'border-blue-500/30' },
  'SINGLES': { name: 'SINGLES', maxDisplay: 2, color: 'text-cyan-400', borderColor: 'border-cyan-500/30' },
  'LIVE VERSIONS': { name: 'LIVE VERSIONS', maxDisplay: 2, color: 'text-pink-400', borderColor: 'border-pink-500/30' },
  'COMPILATIONS': { name: 'COMPILATIONS', maxDisplay: 2, color: 'text-teal-400', borderColor: 'border-teal-500/30' },
  'VINYL RIPS': { name: 'VINYL RIPS', maxDisplay: 2, color: 'text-orange-400', borderColor: 'border-orange-500/30' },
  'SPOTIFY': { name: 'SPOTIFY', maxDisplay: 3, color: 'text-green-400', borderColor: 'border-green-500/30' },
  'YOUTUBE': { name: 'YOUTUBE', maxDisplay: 3, color: 'text-red-400', borderColor: 'border-red-500/30' },
  'OTHER': { name: 'OTHER', maxDisplay: 3, color: 'text-muted-foreground', borderColor: 'border-border' },
}

export const CATEGORY_ORDER = [
  'BEST CHOICE',
  'HI-RES 24-BIT',
  'STUDIO ALBUMS',
  'SINGLES',
  'LIVE VERSIONS',
  'COMPILATIONS',
  'VINYL RIPS',
  'SPOTIFY',
  'YOUTUBE',
  'OTHER'
]

// Categorize a single result based on flacfetch logic
export function categorizeResult(result: ExtendedAudioSearchResult): string {
  const isLossless = result.is_lossless === true
  const is24Bit = result.quality_data?.bit_depth === 24
  const seeders = result.seeders ?? 0
  const provider = result.provider?.toLowerCase() ?? ''
  const releaseType = result.release_type?.toLowerCase() ?? ''
  const media = result.quality_data?.media?.toLowerCase() ?? ''

  // Spotify (higher quality than YouTube, separate category)
  if (provider === 'spotify') {
    return 'SPOTIFY'
  }

  // YouTube and lossy sources
  if (provider === 'youtube' || !isLossless) {
    return 'YOUTUBE'
  }

  // Vinyl rips — check BEFORE best choice so high-seeder vinyl doesn't get recommended
  // (vinyl surface noise makes them poor sources for karaoke)
  if (isLossless && media === 'vinyl') {
    return 'VINYL RIPS'
  }

  // Best choice (50+ seeders, lossless, non-vinyl)
  if (isLossless && seeders >= 50) {
    return 'BEST CHOICE'
  }

  // Hi-res 24-bit
  if (isLossless && is24Bit) {
    return 'HI-RES 24-BIT'
  }

  // Live versions
  if (isLossless && (releaseType === 'live album' || releaseType === 'bootleg' || releaseType.includes('live'))) {
    return 'LIVE VERSIONS'
  }

  // Compilations
  if (isLossless && (releaseType === 'compilation' || releaseType === 'soundtrack' || releaseType === 'anthology')) {
    return 'COMPILATIONS'
  }

  // Singles/EPs
  if (isLossless && (releaseType === 'single' || releaseType === 'ep')) {
    return 'SINGLES'
  }

  // Studio albums
  if (isLossless && (releaseType === 'album' || !releaseType)) {
    return 'STUDIO ALBUMS'
  }

  return 'OTHER'
}

export interface GroupedCategory {
  category: string
  results: ExtendedAudioSearchResult[]
  config: CategoryConfig
}

// Group results by category and sort by category order
export function groupResults(results: ExtendedAudioSearchResult[]): GroupedCategory[] {
  const groups: Record<string, ExtendedAudioSearchResult[]> = {}

  for (const result of results) {
    const category = categorizeResult(result)
    if (!groups[category]) {
      groups[category] = []
    }
    groups[category].push(result)
  }

  const sortedGroups: GroupedCategory[] = []
  for (const cat of CATEGORY_ORDER) {
    if (groups[cat] && groups[cat].length > 0) {
      sortedGroups.push({
        category: cat,
        results: groups[cat],
        config: CATEGORY_CONFIG[cat]
      })
    }
  }

  return sortedGroups
}

// Priority order for picking the best result (lower index = higher priority)
const BEST_RESULT_PRIORITY = [
  'BEST CHOICE',
  'STUDIO ALBUMS',
  'HI-RES 24-BIT',
  'SINGLES',
  'COMPILATIONS',
  'SPOTIFY',
  'YOUTUBE',
  'OTHER',
]

// Returns the single best result from the array
// Priority: BEST CHOICE > STUDIO ALBUMS > HI-RES 24-BIT > SINGLES > COMPILATIONS > SPOTIFY > YOUTUBE > OTHER
// Skips VINYL RIPS (surface noise issues for karaoke) and LIVE VERSIONS (unless nothing else)
// When searchTitle is given and any torrent/Spotify result's track name matches
// it, only those are considered — a mismatched file is usually a different song,
// so the right track from Spotify beats the wrong track from a better-seeded
// torrent. YouTube never wins this way: video titles nearly always "match".
// Within a category, prefers highest seeders, then popularity (view_count).
export function getBestResult(
  results: ExtendedAudioSearchResult[],
  searchTitle = '',
  searchArtist = ''
): ExtendedAudioSearchResult | null {
  if (results.length === 0) return null

  const titleMatches = searchTitle
    ? results.filter(r => !['YOUTUBE', 'VINYL RIPS'].includes(categorizeResult(r))
        && isConfirmedTitleMatch(searchTitle, r))
    : []
  // Same title by the requested artist beats a more popular cover/namesake
  // (George Strait's "The Chair" vs Braxton Keith's).
  const artistMatches = searchArtist ? titleMatches.filter(r => isArtistMatch(searchArtist, r)) : []
  const pool = artistMatches.length > 0 ? artistMatches : titleMatches.length > 0 ? titleMatches : results

  let best: ExtendedAudioSearchResult | null = null
  let bestPriority = Infinity

  for (const result of pool) {
    const category = categorizeResult(result)

    // Skip vinyl rips entirely
    if (category === 'VINYL RIPS') continue

    const priority = BEST_RESULT_PRIORITY.indexOf(category)
    const effectivePriority = priority === -1 ? Infinity : priority

    if (effectivePriority < bestPriority) {
      best = result
      bestPriority = effectivePriority
    } else if (effectivePriority === bestPriority && best) {
      // Same category — prefer higher seeders, then more popular
      const currentSeeders = result.seeders ?? 0
      const bestSeeders = best.seeders ?? 0
      if (currentSeeders > bestSeeders
          || (currentSeeders === bestSeeders && (result.view_count ?? 0) > (best.view_count ?? 0))) {
        best = result
      }
    }
  }

  // If nothing ranked (all vinyl rips / live), stay within the title matches
  return best ?? pool[0]
}

// --- Confidence tier types and utilities ---

export type ConfidenceTier = 1 | 2 | 3

export interface SearchConfidence {
  tier: ConfidenceTier
  reason: string
  bestResult: ExtendedAudioSearchResult | null
  bestCategory: string | null
  warnings: string[]
}

export interface FilenameMismatchResult {
  isMismatch: boolean
  searchTitle: string
  filename: string
  suggestedTrack?: string
}

/**
 * Check if a result's track name doesn't match the search title.
 * Uses target_file for torrent/Spotify results, result.title for YouTube.
 */
const AUDIO_EXT_RE = /\.(flac|mp3|m4a|wav|ogg|opus|aac|alac|ape|wv|aiff?|dsf|mp4|webm)$/i

export function checkFilenameMismatch(
  searchTitle: string,
  result: ExtendedAudioSearchResult
): FilenameMismatchResult {
  const empty: FilenameMismatchResult = { isMismatch: false, searchTitle, filename: '' }

  if (searchTitle.length < 3) return empty

  // Determine the comparison string:
  // - For results with target_file: use the filename (track name from torrent/Spotify)
  // - For YouTube results (no target_file): use result.title (video/track title)
  let filename: string
  if (result.target_file) {
    // Extract filename: strip directory path
    const rawFilename = result.target_file.split('/').pop() || result.target_file
    // Strip extension
    // Only real audio extensions — Spotify track names have none ("Mr. Brightside")
    const withoutExt = rawFilename.replace(AUDIO_EXT_RE, '')
    // Strip leading track number prefixes like "01 - ", "01. ", "1 ", "01-"
    filename = withoutExt.replace(/^\d{1,3}\s*[-.\s]\s*/, '')
  } else if (result.title) {
    filename = result.title
  } else {
    return empty
  }

  // Normalize for comparison: treat underscores, hyphens, dots as word separators
  const normalize = (s: string) => s.toLowerCase().replace(/[_\-.]+/g, ' ').replace(/[^a-z0-9\s]/g, '').replace(/\s+/g, ' ').trim()

  const normTitle = normalize(searchTitle)
  const normFile = normalize(filename)

  // Can't compare if filename normalizes to empty (e.g. all non-Latin characters)
  if (!normFile) return empty

  // Substring match — but require the shorter string to be at least 3 chars
  // to avoid false positives like filename "K" matching title "KoREH"
  const shorter = normFile.length <= normTitle.length ? normFile : normTitle
  const longer = normFile.length <= normTitle.length ? normTitle : normFile
  const matches = shorter.length >= 3 && longer.includes(shorter)

  if (matches) return { isMismatch: false, searchTitle, filename }

  return {
    isMismatch: true,
    searchTitle,
    filename,
    suggestedTrack: filename,
  }
}

// Whole-title core for equality: drops only TRAILING version suffixes —
// "The Chair (feat. X)", "Hotel California - 2013 Remaster" — so "The Chairman"
// doesn't match "The Chair" and "(I Can't Get No) Satisfaction" stays intact.
function titleCore(s: string): string {
  const norm = (x: string) => x.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()
  let c = s
  for (let i = 0; i < 3; i++) {
    const next = c.replace(/\s*[([][^)\]]*[)\]]\s*$/, '').replace(/\s+-\s+[^-]*$/, '')
    if (next === c) break
    c = next
  }
  const core = norm(c)
  return core.length >= 3 ? core : norm(s)
}

/**
 * Whether the result's artist is the requested artist. Lenient on purpose:
 * "Braxton Keith, Someone" / "The Killers" vs "Killers" still match.
 */
export function isArtistMatch(searchArtist: string, result: ExtendedAudioSearchResult): boolean {
  const norm = (x: string) => x.toLowerCase().replace(/^the\s+/, '').replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()
  const want = norm(searchArtist)
  const have = norm(result.artist || '')
  if (want.length < 2 || !have) return false
  if (have === want || ` ${have} `.includes(` ${want} `) || ` ${want} `.includes(` ${have} `)) return true
  // Small typos the singer's tidy didn't fix ("radiohed")
  return editDistance(want, have) <= Math.max(1, Math.floor(want.length / 5))
}

function editDistance(a: string, b: string): number {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    const cur = [i]
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    }
    prev = cur
  }
  return prev[b.length]
}

/**
 * True only when the result's track filename was actually compared and matched.
 * checkFilenameMismatch reports "no mismatch" when it can't compare (no
 * target_file → album title, title < 3 chars, non-Latin filename), which must not
 * count as evidence that this is the right track.
 */
export function isConfirmedTitleMatch(searchTitle: string, result: ExtendedAudioSearchResult): boolean {
  if (!result.target_file) return false
  const m = checkFilenameMismatch(searchTitle, result)
  if (m.isMismatch || !m.filename || searchTitle.length < 3) return false
  const t = titleCore(searchTitle)
  if (t.length < 3) return false
  // Compare with and without the track-number strip ("7 Rings" must keep its 7)
  const raw = (result.target_file.split('/').pop() || result.target_file).replace(AUDIO_EXT_RE, '')
  return titleCore(m.filename) === t || titleCore(raw) === t
}

/**
 * Get a human-readable availability label + tooltip for a seeder count.
 */
export function getAvailabilityLabel(seeders: number | undefined | null): { text: string; tooltip: string } {
  if (seeders === undefined || seeders === null) return { text: '', tooltip: '' }

  if (seeders >= 50) return { text: 'High', tooltip: 'Well-seeded, downloads reliably' }
  if (seeders >= 10) return { text: 'Medium', tooltip: 'Should download but may be slower' }
  return { text: 'Low', tooltip: 'Few seeders — may take a bit more time to prepare' }
}

/**
 * Analyze search results and return a confidence assessment with tier, reasoning, and warnings.
 */
export function getSearchConfidence(
  results: ExtendedAudioSearchResult[],
  searchTitle: string,
  searchArtist = ''
): SearchConfidence {
  if (results.length === 0) {
    return {
      tier: 3,
      reason: 'No audio sources found for this song.',
      bestResult: null,
      bestCategory: null,
      warnings: [],
    }
  }

  const best = getBestResult(results, searchTitle, searchArtist)
  const bestCat = best ? categorizeResult(best) : null
  const warnings: string[] = []

  // Check filename mismatch on best result
  let bestHasMismatch = false
  if (best) {
    const mismatch = checkFilenameMismatch(searchTitle, best)
    if (mismatch.isMismatch) {
      bestHasMismatch = true
      const suggestion = mismatch.suggestedTrack
        ? `Filename suggests different track: ${mismatch.suggestedTrack}`
        : 'Filename may not match your song'
      warnings.push(suggestion)
    }
  }

  // Check if only lossy/vinyl results exist
  const hasLossless = results.some(r => {
    const cat = categorizeResult(r)
    return cat !== 'YOUTUBE' && cat !== 'SPOTIFY' && cat !== 'VINYL RIPS'
  })

  // Spotify is an official release (16-bit WEB) — the right track from it is a
  // good source, second only to lossless torrents and far better than YouTube.
  const spotifyMatch = bestCat === 'SPOTIFY' && !!best && isConfirmedTitleMatch(searchTitle, best)
    && (!searchArtist || isArtistMatch(searchArtist, best))

  if (!hasLossless && !spotifyMatch) {
    warnings.push('No lossless sources available — only YouTube/lossy or vinyl rips found')
  }

  // Check availability on best result
  if (best && best.seeders !== undefined && best.seeders !== null && best.seeders < 10) {
    warnings.push('Low availability — may take a bit more time to prepare for your review')
  }

  // Build reason string
  const reason = buildConfidenceReason(best, bestCat)

  // Tier 1: Best result is BEST CHOICE and no filename mismatch
  if (bestCat === 'BEST CHOICE' && !bestHasMismatch) {
    return { tier: 1, reason, bestResult: best, bestCategory: bestCat, warnings }
  }

  // Tier 3: No results, or only lossy/vinyl (bar a Spotify title match), or mismatch with low availability
  if (!hasLossless && !spotifyMatch) {
    return { tier: 3, reason, bestResult: best, bestCategory: bestCat, warnings }
  }

  if (bestHasMismatch && best && (best.seeders === undefined || best.seeders === null || best.seeders < 10)) {
    return { tier: 3, reason, bestResult: best, bestCategory: bestCat, warnings }
  }

  // Tier 2: Has lossless options but not clearly the right pick
  return { tier: 2, reason, bestResult: best, bestCategory: bestCat, warnings }
}

function buildConfidenceReason(
  best: ExtendedAudioSearchResult | null,
  bestCat: string | null
): string {
  if (!best || !bestCat) return 'No suitable audio sources found.'

  const parts: string[] = []

  // Quality descriptor
  if (bestCat === 'BEST CHOICE') {
    parts.push('High-quality lossless')
  } else if (best.is_lossless) {
    parts.push('Lossless')
  } else if (bestCat === 'SPOTIFY') {
    parts.push('Spotify audio')
  } else if (best.provider === 'YouTube') {
    parts.push('YouTube audio')
  } else {
    parts.push('Lossy audio')
  }

  // Album/year context
  if (best.title) {
    const albumParts: string[] = []
    if (best.release_type) albumParts.push(best.release_type)
    if ((best as any).year) albumParts.push(`${(best as any).year}`)
    if (albumParts.length > 0) {
      parts.push(`from ${albumParts.join(', ')}`)
    }
  }

  // Availability
  if (best.seeders !== undefined && best.seeders !== null) {
    if (best.seeders >= 50) parts.push('reliable download')
    else if (best.seeders >= 10) parts.push('available but may be slower')
    else parts.push('low availability')
  }

  return parts.join(', ')
}

// Get display name - use channel for YouTube, artist for others
export function getDisplayName(result: ExtendedAudioSearchResult): string {
  if (result.provider === "YouTube" && result.channel) {
    return result.channel
  }
  return result.artist || ""
}

// Format views/seeders compactly
export function formatCount(count?: number): string {
  if (!count && count !== 0) return "-"
  if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`
  if (count >= 1000) return `${(count / 1000).toFixed(1)}K`
  return count.toString()
}

// Format release metadata line
export function formatMetadata(result: ExtendedAudioSearchResult): string {
  const parts: string[] = []

  if (result.release_type) parts.push(result.release_type)
  if (result.year) parts.push(result.year.toString())
  if (result.label) parts.push(result.label)
  if (result.edition_info) parts.push(result.edition_info)
  if (result.quality_data?.media) parts.push(result.quality_data.media)

  return parts.length > 0 ? `[${parts.join(' / ')}]` : ''
}

// Get quality display string
export function formatQuality(result: ExtendedAudioSearchResult): string {
  if (result.quality) return result.quality

  const qd = result.quality_data
  if (!qd) return '-'

  const parts: string[] = []
  if (qd.format) parts.push(qd.format)
  if (qd.bit_depth) parts.push(`${qd.bit_depth}bit`)
  if (qd.bitrate) parts.push(`${qd.bitrate}kbps`)
  if (qd.media) parts.push(qd.media)

  return parts.join(' ') || '-'
}
