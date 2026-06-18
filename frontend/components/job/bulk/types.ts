export interface BulkSongRow {
  key: string
  artist: string
  title: string
  display_artist?: string
  display_title?: string
  selected: boolean
  available?: boolean | null
  brands?: string[]
  is_extra?: boolean
  length_ms?: number | null
}

/**
 * Whether an album track should start ticked. Extras (live/remix/bonus) and
 * tracks that already have a community version start UNticked (the user can
 * re-tick them).
 */
export function shouldSelectTrack(track: { is_extra?: boolean; available?: boolean | null }): boolean {
  return !(track.is_extra || track.available)
}

/**
 * Credit gate for the submit button: 1 credit/song minimum. Admins and unknown
 * balances bypass. Returns false outside 1..maxSongs.
 */
export function canSubmitBatch(
  selectedCount: number,
  credits: number | null,
  isAdmin: boolean,
  maxSongs: number
): boolean {
  if (selectedCount <= 0 || selectedCount > maxSongs) return false
  if (isAdmin || credits == null) return true
  return credits >= selectedCount
}

export function newRow(partial: Partial<BulkSongRow> = {}): BulkSongRow {
  return {
    key: Math.random().toString(36).slice(2),
    artist: "",
    title: "",
    selected: true,
    available: null,
    brands: [],
    is_extra: false,
    ...partial,
  }
}
