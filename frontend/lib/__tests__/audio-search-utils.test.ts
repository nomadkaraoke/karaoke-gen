import {
  categorizeResult,
  groupResults,
  getBestResult,
  getDisplayName,
  formatCount,
  formatMetadata,
  formatQuality,
  checkFilenameMismatch,
  getAvailabilityLabel,
  getSearchConfidence,
  isConfirmedTitleMatch,
  isArtistMatch,
  ExtendedAudioSearchResult,
} from '../audio-search-utils'

// Helper to create a minimal result with overrides
function makeResult(overrides: Partial<ExtendedAudioSearchResult> & { index: number }): ExtendedAudioSearchResult {
  return {
    title: 'Test Song',
    artist: 'Test Artist',
    is_lossless: false,
    provider: 'other',
    ...overrides,
  } as ExtendedAudioSearchResult
}

describe('categorizeResult', () => {
  it('categorizes YouTube provider as YOUTUBE', () => {
    const result = makeResult({ index: 0, provider: 'YouTube', is_lossless: false })
    expect(categorizeResult(result)).toBe('YOUTUBE')
  })

  it('categorizes non-lossless as YOUTUBE', () => {
    const result = makeResult({ index: 0, is_lossless: false })
    expect(categorizeResult(result)).toBe('YOUTUBE')
  })

  it('categorizes lossless with 50+ seeders as BEST CHOICE', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 55 })
    expect(categorizeResult(result)).toBe('BEST CHOICE')
  })

  it('categorizes lossless 24-bit as HI-RES 24-BIT', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, quality_data: { bit_depth: 24 } })
    expect(categorizeResult(result)).toBe('HI-RES 24-BIT')
  })

  it('categorizes lossless vinyl media as VINYL RIPS', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, quality_data: { media: 'Vinyl' } })
    expect(categorizeResult(result)).toBe('VINYL RIPS')
  })

  it('categorizes vinyl with 50+ seeders as VINYL RIPS (not BEST CHOICE)', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 100, quality_data: { media: 'Vinyl' } })
    expect(categorizeResult(result)).toBe('VINYL RIPS')
  })

  it('categorizes lossless live album as LIVE VERSIONS', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, release_type: 'Live Album' })
    expect(categorizeResult(result)).toBe('LIVE VERSIONS')
  })

  it('categorizes lossless compilation as COMPILATIONS', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, release_type: 'Compilation' })
    expect(categorizeResult(result)).toBe('COMPILATIONS')
  })

  it('categorizes lossless single as SINGLES', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, release_type: 'Single' })
    expect(categorizeResult(result)).toBe('SINGLES')
  })

  it('categorizes lossless album as STUDIO ALBUMS', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, release_type: 'Album' })
    expect(categorizeResult(result)).toBe('STUDIO ALBUMS')
  })

  it('categorizes lossless with no release type as STUDIO ALBUMS', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5 })
    expect(categorizeResult(result)).toBe('STUDIO ALBUMS')
  })

  it('categorizes lossless with unknown release type as OTHER', () => {
    const result = makeResult({ index: 0, is_lossless: true, seeders: 5, release_type: 'Interview' })
    expect(categorizeResult(result)).toBe('OTHER')
  })

  it('categorizes Spotify provider as SPOTIFY', () => {
    const result = makeResult({ index: 0, provider: 'Spotify', is_lossless: false })
    expect(categorizeResult(result)).toBe('SPOTIFY')
  })

  it('categorizes Spotify before checking lossless status', () => {
    // Spotify results report FLAC quality but is_lossless may vary
    const result = makeResult({ index: 0, provider: 'Spotify', is_lossless: true, seeders: 100 })
    expect(categorizeResult(result)).toBe('SPOTIFY')
  })
})

describe('getBestResult', () => {
  it('returns null for empty array', () => {
    expect(getBestResult([])).toBeNull()
  })

  it('returns the only result when there is one', () => {
    const result = makeResult({ index: 0, is_lossless: false })
    expect(getBestResult([result])).toBe(result)
  })

  it('prefers BEST CHOICE over STUDIO ALBUMS', () => {
    const bestChoice = makeResult({ index: 0, is_lossless: true, seeders: 100, release_type: 'Album' })
    const studioAlbum = makeResult({ index: 1, is_lossless: true, seeders: 10, release_type: 'Album' })
    expect(getBestResult([studioAlbum, bestChoice])).toBe(bestChoice)
  })

  it('prefers STUDIO ALBUMS over YOUTUBE', () => {
    const studio = makeResult({ index: 0, is_lossless: true, seeders: 10, release_type: 'Album' })
    const youtube = makeResult({ index: 1, provider: 'YouTube', is_lossless: false })
    expect(getBestResult([youtube, studio])).toBe(studio)
  })

  it('prefers higher seeders within same category', () => {
    const lowSeeders = makeResult({ index: 0, is_lossless: true, seeders: 5, release_type: 'Album' })
    const highSeeders = makeResult({ index: 1, is_lossless: true, seeders: 30, release_type: 'Album' })
    expect(getBestResult([lowSeeders, highSeeders])).toBe(highSeeders)
  })

  it('skips VINYL RIPS when other options exist', () => {
    // Use low seeders so vinyl is categorized as VINYL RIPS (not BEST CHOICE)
    const vinyl = makeResult({ index: 0, is_lossless: true, seeders: 10, quality_data: { media: 'Vinyl' } })
    const studio = makeResult({ index: 1, is_lossless: true, seeders: 5, release_type: 'Album' })
    expect(getBestResult([vinyl, studio])).toBe(studio)
  })

  it('falls back to first result when all are VINYL RIPS', () => {
    const vinyl1 = makeResult({ index: 0, is_lossless: true, seeders: 10, quality_data: { media: 'Vinyl' } })
    const vinyl2 = makeResult({ index: 1, is_lossless: true, seeders: 20, quality_data: { media: 'Vinyl' } })
    expect(getBestResult([vinyl1, vinyl2])).toBe(vinyl1)
  })

  it('prefers SINGLES over YOUTUBE', () => {
    const single = makeResult({ index: 0, is_lossless: true, seeders: 3, release_type: 'Single' })
    const youtube = makeResult({ index: 1, provider: 'YouTube', is_lossless: false, view_count: 1000000 })
    expect(getBestResult([youtube, single])).toBe(single)
  })

  it('handles mixed results and picks best category', () => {
    const results = [
      makeResult({ index: 0, provider: 'YouTube', is_lossless: false }), // YOUTUBE
      makeResult({ index: 1, is_lossless: true, seeders: 3, release_type: 'Single' }), // SINGLES
      makeResult({ index: 2, is_lossless: true, seeders: 80 }), // BEST CHOICE
      makeResult({ index: 3, is_lossless: true, seeders: 5, quality_data: { media: 'Vinyl' } }), // VINYL (skipped)
    ]
    expect(getBestResult(results)?.index).toBe(2)
  })
})

describe('groupResults', () => {
  it('returns empty array for empty input', () => {
    expect(groupResults([])).toEqual([])
  })

  it('groups results by category', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 100 }), // BEST CHOICE
      makeResult({ index: 1, is_lossless: true, seeders: 10, release_type: 'Album' }), // STUDIO ALBUMS
      makeResult({ index: 2, is_lossless: true, seeders: 60 }), // BEST CHOICE
    ]
    const groups = groupResults(results)
    expect(groups).toHaveLength(2)
    expect(groups[0].category).toBe('BEST CHOICE')
    expect(groups[0].results).toHaveLength(2)
    expect(groups[1].category).toBe('STUDIO ALBUMS')
    expect(groups[1].results).toHaveLength(1)
  })

  it('sorts groups by category order', () => {
    const results = [
      makeResult({ index: 0, provider: 'YouTube', is_lossless: false }), // YOUTUBE (lower priority)
      makeResult({ index: 1, is_lossless: true, seeders: 100 }), // BEST CHOICE (higher priority)
    ]
    const groups = groupResults(results)
    expect(groups[0].category).toBe('BEST CHOICE')
    expect(groups[1].category).toBe('YOUTUBE')
  })

  it('includes config for each group', () => {
    const results = [makeResult({ index: 0, is_lossless: true, seeders: 100 })]
    const groups = groupResults(results)
    expect(groups[0].config).toBeDefined()
    expect(groups[0].config.color).toBeDefined()
    expect(groups[0].config.maxDisplay).toBeGreaterThan(0)
  })
})

describe('getDisplayName', () => {
  it('returns channel for YouTube results', () => {
    const result = makeResult({ index: 0, provider: 'YouTube', channel: 'VEVO' })
    expect(getDisplayName(result)).toBe('VEVO')
  })

  it('returns artist for non-YouTube results', () => {
    const result = makeResult({ index: 0, artist: 'Queen' })
    expect(getDisplayName(result)).toBe('Queen')
  })

  it('returns empty string when no artist', () => {
    const result = makeResult({ index: 0, artist: undefined } as any)
    expect(getDisplayName(result)).toBe('')
  })
})

describe('formatCount', () => {
  it('returns dash for undefined', () => {
    expect(formatCount(undefined)).toBe('-')
  })

  it('formats millions', () => {
    expect(formatCount(1500000)).toBe('1.5M')
  })

  it('formats thousands', () => {
    expect(formatCount(5600)).toBe('5.6K')
  })

  it('returns raw number for small counts', () => {
    expect(formatCount(42)).toBe('42')
  })

  it('returns 0 for zero', () => {
    expect(formatCount(0)).toBe('0')
  })
})

describe('formatMetadata', () => {
  it('returns empty string when no metadata', () => {
    const result = makeResult({ index: 0 })
    expect(formatMetadata(result)).toBe('')
  })

  it('formats release type and year', () => {
    const result = makeResult({ index: 0, release_type: 'Album', year: 1975 } as any)
    expect(formatMetadata(result)).toBe('[Album / 1975]')
  })

  it('includes label and edition info', () => {
    const result = makeResult({ index: 0, release_type: 'Album', label: 'EMI', edition_info: 'Remaster' })
    expect(formatMetadata(result)).toBe('[Album / EMI / Remaster]')
  })
})

describe('formatQuality', () => {
  it('returns quality string when present', () => {
    const result = makeResult({ index: 0, quality: 'FLAC 16bit 44.1kHz' } as any)
    expect(formatQuality(result)).toBe('FLAC 16bit 44.1kHz')
  })

  it('builds quality from quality_data', () => {
    const result = makeResult({ index: 0, quality_data: { format: 'FLAC', bit_depth: 16, bitrate: 1411 } })
    expect(formatQuality(result)).toBe('FLAC 16bit 1411kbps')
  })

  it('returns dash when no quality info', () => {
    const result = makeResult({ index: 0 })
    expect(formatQuality(result)).toBe('-')
  })
})

describe('checkFilenameMismatch', () => {
  it('returns no mismatch when filename contains search title', () => {
    const result = makeResult({ index: 0, target_file: 'Coldplay/Parachutes/01 - Yellow.flac' })
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.isMismatch).toBe(false)
  })

  it('returns no mismatch when search title contains filename', () => {
    const result = makeResult({ index: 0, target_file: 'Yellow.flac' })
    const check = checkFilenameMismatch('Yellow (Radio Edit)', result)
    expect(check.isMismatch).toBe(false)
  })

  it('detects mismatch when filename is a different track', () => {
    const result = makeResult({ index: 0, target_file: 'Coldplay/Parachutes/03 - Spies.flac' })
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.isMismatch).toBe(true)
    expect(check.suggestedTrack).toBe('Spies')
  })

  it('strips track number prefixes from filename', () => {
    const result = makeResult({ index: 0, target_file: '01 - Yellow.flac' })
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.isMismatch).toBe(false)
  })

  it('strips "01. " prefix', () => {
    const result = makeResult({ index: 0, target_file: '01. Yellow.flac' })
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.isMismatch).toBe(false)
  })

  it('is case-insensitive', () => {
    const result = makeResult({ index: 0, target_file: 'YELLOW.flac' })
    const check = checkFilenameMismatch('yellow', result)
    expect(check.isMismatch).toBe(false)
  })

  it('ignores punctuation differences', () => {
    const result = makeResult({ index: 0, target_file: "Don't Look Back in Anger.flac" })
    const check = checkFilenameMismatch('Dont Look Back in Anger', result)
    expect(check.isMismatch).toBe(false)
  })

  it('checks YouTube results using result.title instead of target_file', () => {
    const matchingResult = makeResult({ index: 0, provider: 'YouTube', title: 'Yellow' })
    expect(checkFilenameMismatch('Yellow', matchingResult).isMismatch).toBe(false)

    const mismatchResult = makeResult({ index: 0, provider: 'YouTube', title: 'Something Else' })
    expect(checkFilenameMismatch('Yellow', mismatchResult).isMismatch).toBe(true)
  })

  it('treats underscores as word separators', () => {
    const result = makeResult({ index: 0, target_file: '10-Pyro_Sets_A_Wildfire.flac' })
    const check = checkFilenameMismatch('Pyro Sets a Wildfire', result)
    expect(check.isMismatch).toBe(false)
  })

  it('treats hyphens and dots as word separators', () => {
    const result = makeResult({ index: 0, target_file: 'My-Song.Name.Here.flac' })
    const check = checkFilenameMismatch('My Song Name Here', result)
    expect(check.isMismatch).toBe(false)
  })

  it('returns indeterminate for non-Latin filenames (no false match)', () => {
    const result = makeResult({ index: 0, target_file: 'תמיד אוהב אותי' })
    const check = checkFilenameMismatch('I Love You, KoREH', result)
    // Can't determine match — should return isMismatch: false with empty filename
    expect(check.isMismatch).toBe(false)
    expect(check.filename).toBe('')
  })

  it('rejects very short filenames to avoid false substring matches', () => {
    const result = makeResult({ index: 0, target_file: 'K.' })
    const check = checkFilenameMismatch('I Love You, KoREH', result)
    expect(check.isMismatch).toBe(true)
  })

  it('uses result.title for Spotify results without target_file', () => {
    const result = makeResult({ index: 0, provider: 'Spotify', title: 'Yellow', target_file: undefined })
    // Falls back to title since no target_file
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.isMismatch).toBe(false)
  })

  it('prefers target_file over title when both exist', () => {
    const result = makeResult({ index: 0, provider: 'Spotify', title: 'Wrong Album', target_file: 'Yellow.flac' })
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.isMismatch).toBe(false)
  })

  it('skips check for very short titles (< 3 chars)', () => {
    const result = makeResult({ index: 0, target_file: 'Something Else.flac' })
    const check = checkFilenameMismatch('Go', result)
    expect(check.isMismatch).toBe(false)
  })

  it('returns filename in result', () => {
    const result = makeResult({ index: 0, target_file: 'Coldplay/03 - Spies.flac' })
    const check = checkFilenameMismatch('Yellow', result)
    expect(check.filename).toBe('Spies')
  })
})

describe('getAvailabilityLabel', () => {
  it('returns High with tooltip for 50+ seeders', () => {
    const label = getAvailabilityLabel(75)
    expect(label.text).toBe('High')
    expect(label.tooltip).toBe('Well-seeded, downloads reliably')
  })

  it('returns Medium with tooltip for 10-49 seeders', () => {
    const label = getAvailabilityLabel(25)
    expect(label.text).toBe('Medium')
    expect(label.tooltip).toBe('Should download but may be slower')
  })

  it('returns Low with tooltip for < 10 seeders', () => {
    const label = getAvailabilityLabel(3)
    expect(label.text).toBe('Low')
    expect(label.tooltip).toBe('Few seeders — may take a bit more time to prepare')
  })

  it('returns empty for undefined', () => {
    const label = getAvailabilityLabel(undefined)
    expect(label.text).toBe('')
    expect(label.tooltip).toBe('')
  })

  it('returns empty for null', () => {
    const label = getAvailabilityLabel(null)
    expect(label.text).toBe('')
    expect(label.tooltip).toBe('')
  })

  it('returns Low for 0 seeders', () => {
    const label = getAvailabilityLabel(0)
    expect(label.text).toBe('Low')
  })

  it('returns High for exactly 50 seeders', () => {
    const label = getAvailabilityLabel(50)
    expect(label.text).toBe('High')
  })

  it('returns Medium for exactly 10 seeders', () => {
    const label = getAvailabilityLabel(10)
    expect(label.text).toBe('Medium')
  })
})

describe('getSearchConfidence', () => {
  it('returns tier 3 for empty results', () => {
    const confidence = getSearchConfidence([], 'Yellow')
    expect(confidence.tier).toBe(3)
    expect(confidence.bestResult).toBeNull()
    expect(confidence.bestCategory).toBeNull()
  })

  it('returns tier 1 for BEST CHOICE with no mismatch', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 100, target_file: '01 - Yellow.flac' }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.tier).toBe(1)
    expect(confidence.bestCategory).toBe('BEST CHOICE')
    expect(confidence.warnings).toHaveLength(0)
  })

  it('returns tier 2 when BEST CHOICE has filename mismatch (but high availability)', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 100, target_file: '03 - Spies.flac' }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.tier).toBe(2)
    expect(confidence.warnings.length).toBeGreaterThan(0)
    expect(confidence.warnings.some(w => w.includes('Spies'))).toBe(true)
  })

  it('returns tier 2 for studio album with moderate seeders', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 20, release_type: 'Album', target_file: '01 - Yellow.flac' }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.tier).toBe(2)
    expect(confidence.bestCategory).toBe('STUDIO ALBUMS')
  })

  it('returns tier 3 when only YouTube/lossy results', () => {
    const results = [
      makeResult({ index: 0, provider: 'YouTube', is_lossless: false }),
      makeResult({ index: 1, is_lossless: false }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.tier).toBe(3)
    expect(confidence.warnings.some(w => w.includes('lossless'))).toBe(true)
  })

  it('returns tier 3 when only vinyl rips', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 20, quality_data: { media: 'Vinyl' } }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.tier).toBe(3)
  })

  it('returns tier 3 for mismatch + low availability', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 3, target_file: '03 - Spies.flac' }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.tier).toBe(3)
    expect(confidence.warnings.some(w => w.includes('Spies'))).toBe(true)
    expect(confidence.warnings.some(w => w.includes('availability'))).toBe(true)
  })

  it('includes low availability warning when seeders < 10', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 5, target_file: '01 - Yellow.flac' }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.warnings.some(w => w.includes('availability'))).toBe(true)
  })

  it('builds reason string with quality context', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 100, release_type: 'Album', target_file: '01 - Yellow.flac' }),
    ]
    const confidence = getSearchConfidence(results, 'Yellow')
    expect(confidence.reason).toContain('lossless')
  })

  it('bestResult matches getBestResult output', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 100 }),
      makeResult({ index: 1, is_lossless: true, seeders: 5, release_type: 'Album' }),
    ]
    const confidence = getSearchConfidence(results, 'Test Song')
    expect(confidence.bestResult?.index).toBe(0)
  })
})

// Real search from a live kjbox night (2026-10-01): "Braxton Keith - The Chair"
// returned only Spotify results. `title` is the album, `target_file` the track.
// The UI used to call this "Limited sources found" and push the singer to YouTube.
const spotify = (index: number, album: string, track: string, popularity: number, release_type = 'Album') =>
  makeResult({
    index, provider: 'Spotify', title: album, target_file: track, artist: 'Braxton Keith',
    quality: 'FLAC 16bit WEB', release_type, view_count: popularity * 10000,
  })
const braxtonKeithResults = () => [
  spotify(0, 'Real Damn Deal', 'I Own This Bar', 40),
  spotify(1, 'Cozy', 'Cozy', 45),
  spotify(2, 'The Chair', 'The Chair', 65, 'Single'),
  spotify(3, 'I Own This Bar', 'I Own This Bar', 40),
  spotify(4, 'The Chair', 'Prescription', 20, 'Single'),
  spotify(5, 'Highway Country', 'The Chair', 23, 'Compilation'),
  spotify(6, 'pov: U fell in love', 'The Chair', 14, 'Compilation'),
]

describe('title-matched best result', () => {
  it('picks the Spotify track whose name matches, most popular first', () => {
    expect(getBestResult(braxtonKeithResults(), 'The Chair')?.index).toBe(2)
  })

  it('without a title keeps the old category/seeders behaviour', () => {
    expect(getBestResult(braxtonKeithResults())?.index).toBe(2) // popularity tiebreak
  })

  it('a Spotify title match is a confident tier 2, not "limited sources"', () => {
    const c = getSearchConfidence(braxtonKeithResults(), 'The Chair')
    expect(c.tier).toBe(2)
    expect(c.bestResult?.index).toBe(2)
    expect(c.warnings).toEqual([])
    expect(c.reason).toContain('Spotify')
  })

  it('Spotify with no matching track is still tier 3', () => {
    const results = [spotify(0, 'Real Damn Deal', 'I Own This Bar', 40)]
    expect(getSearchConfidence(results, 'The Chair').tier).toBe(3)
  })

  it('prefers the right track on Spotify over the wrong track on a torrent', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 120, release_type: 'Album', target_file: '03 - Prescription.flac' }),
      spotify(1, 'The Chair', 'The Chair', 65, 'Single'),
    ]
    expect(getBestResult(results, 'The Chair')?.index).toBe(1)
  })

  it('still prefers a matching torrent over matching Spotify', () => {
    const results = [
      spotify(0, 'The Chair', 'The Chair', 65, 'Single'),
      makeResult({ index: 1, is_lossless: true, seeders: 120, release_type: 'Album', target_file: '01 - The Chair.flac' }),
    ]
    const c = getSearchConfidence(results, 'The Chair')
    expect(c.bestResult?.index).toBe(1)
    expect(c.tier).toBe(1)
  })

  it('a Spotify album named after the song is not a track match', () => {
    const results = [
      makeResult({ index: 0, provider: 'Spotify', title: 'The Chair', artist: 'Braxton Keith' }),
    ]
    const c = getSearchConfidence(results, 'The Chair')
    expect(c.tier).toBe(3)
  })

  it('isConfirmedTitleMatch needs a compared, matching track filename', () => {
    expect(isConfirmedTitleMatch('The Chair', spotify(0, 'X', 'The Chair', 1))).toBe(true)
    expect(isConfirmedTitleMatch('The Chair', spotify(0, 'X', 'Cozy', 1))).toBe(false)
    expect(isConfirmedTitleMatch('Up', spotify(0, 'X', 'Up', 1))).toBe(false)
    expect(isConfirmedTitleMatch('The Chair', spotify(0, 'X', 'The Chairman', 1))).toBe(false)
    expect(isConfirmedTitleMatch('The Chair', spotify(0, 'X', 'The Chair (feat. Someone)', 1))).toBe(true)
    expect(isConfirmedTitleMatch('Hotel California',
      spotify(0, 'X', 'Hotel California - 2013 Remaster', 1))).toBe(true)
    expect(isConfirmedTitleMatch('Mr. Brightside', spotify(0, 'X', 'Mr. Brightside', 1))).toBe(true)
    expect(isConfirmedTitleMatch('7 Rings', spotify(0, 'X', '7 rings', 1))).toBe(true)
    expect(isConfirmedTitleMatch("(I Can't Get No) Satisfaction",
      spotify(0, 'X', "(I Can't Get No) Satisfaction - Mono Version", 1))).toBe(true)
    expect(isConfirmedTitleMatch('The Chair', spotify(0, 'X', 'The Chair (feat. X) [Remastered]', 1))).toBe(true)
    expect(isConfirmedTitleMatch('Creep',
      makeResult({ index: 0, is_lossless: true, target_file: 'Pablo Honey/02 - Creep.flac' }))).toBe(true)
    expect(isConfirmedTitleMatch('The Chair',
      makeResult({ index: 0, provider: 'Spotify', title: 'The Chair' }))).toBe(false)
  })

  it('a title-matched live version beats an unranked fallback to results[0]', () => {
    const results = [
      spotify(0, 'Real Damn Deal', 'I Own This Bar', 40),
      makeResult({ index: 1, is_lossless: true, seeders: 5, release_type: 'Live album', target_file: '04 - The Chair.flac' }),
    ]
    expect(getBestResult(results, 'The Chair')?.index).toBe(1)
  })

  it('prefers the requested artist over a more popular same-title track', () => {
    // Real prod search: George Strait's "The Chair" (popularity 68) outranked
    // Braxton Keith's (65) when only the title was considered.
    const strait = { ...spotify(7, 'Something Special', 'The Chair', 68), artist: 'George Strait' }
    const results = [...braxtonKeithResults(), strait]
    expect(getBestResult(results, 'The Chair')?.index).toBe(7)
    const c = getSearchConfidence(results, 'The Chair', 'Braxton Keith')
    expect(c.bestResult?.index).toBe(2)
    expect(c.tier).toBe(2)
  })

  it('a Spotify title match by another artist is not a confident pick', () => {
    const strait = { ...spotify(0, 'Something Special', 'The Chair', 68), artist: 'George Strait' }
    expect(getSearchConfidence([strait], 'The Chair', 'Braxton Keith').tier).toBe(3)
  })

  it('isArtistMatch is lenient on featured artists and "The"', () => {
    const r = (artist: string) => makeResult({ index: 0, artist })
    expect(isArtistMatch('Braxton Keith', r('Braxton Keith, Someone'))).toBe(true)
    expect(isArtistMatch('Killers', r('The Killers'))).toBe(true)
    expect(isArtistMatch('Braxton Keith', r('George Strait'))).toBe(false)
    expect(isArtistMatch('Kei', r('Braxton Keith'))).toBe(false)
    expect(isArtistMatch('radiohed', r('Radiohead'))).toBe(true)
    expect(isArtistMatch('U2', r('UB40'))).toBe(false)
  })

  it('never promotes YouTube via a title match over a torrent', () => {
    const results = [
      makeResult({ index: 0, is_lossless: true, seeders: 120, release_type: 'Album', target_file: '03 - Prescription.flac' }),
      makeResult({ index: 1, provider: 'YouTube', title: 'Braxton Keith - The Chair (Official Video)' }),
    ]
    expect(getBestResult(results, 'The Chair')?.index).toBe(0)
  })
})
