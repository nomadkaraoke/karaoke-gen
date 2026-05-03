import { CorrectionData, LyricsSegment } from '../types'

const STORAGE_KEY = 'lyrics_analyzer_data'

// Per-entry timestamp lets quota-driven eviction drop the oldest songs first.
// Older saves used the bare CorrectionData; both shapes are read transparently.
interface SavedEntry {
  savedAt: number
  data: CorrectionData
}

type StoredMap = Record<string, SavedEntry | CorrectionData>

// Generate a storage key based on the first segment's text
export const generateStorageKey = (data: CorrectionData): string => {
  const text = data.original_segments[0]?.text || ''
  let hash = 0
  for (let i = 0; i < text.length; i++) {
    const char = text.charCodeAt(i)
    hash = (hash << 5) - hash + char
    hash = hash & hash // Convert to 32-bit integer
  }
  return `song_${hash}`
}

const stripIds = (obj: CorrectionData): LyricsSegment[] => {
  const clone = JSON.parse(JSON.stringify(obj))
  return clone.corrected_segments.map((segment: LyricsSegment) => {
    const { id: _id, ...strippedSegment } = segment
    return {
      ...strippedSegment,
      words: segment.words.map((word) => {
        const { id: _wordId, ...strippedWord } = word
        return strippedWord
      }),
    }
  })
}

function unwrap(entry: SavedEntry | CorrectionData | undefined): CorrectionData | null {
  if (!entry || typeof entry !== 'object') return null
  if ('data' in entry && entry.data && typeof entry.data === 'object' && 'original_segments' in entry.data) {
    return entry.data as CorrectionData
  }
  if ('original_segments' in entry) return entry as CorrectionData
  return null
}

function isQuotaError(e: unknown): boolean {
  if (!(e instanceof Error)) return false
  // QuotaExceededError on Chrome/Firefox; NS_ERROR_DOM_QUOTA_REACHED legacy Firefox.
  // code 22 / 1014 cover older browsers that don't set name.
  const name = e.name
  const code = (e as Error & { code?: number }).code
  return (
    name === 'QuotaExceededError' ||
    name === 'NS_ERROR_DOM_QUOTA_REACHED' ||
    code === 22 ||
    code === 1014
  )
}

function evictOldest(store: StoredMap, protectedKey: string): boolean {
  const candidates = Object.entries(store)
    .filter(([k]) => k !== protectedKey)
    .map(([k, v]) => {
      const savedAt = v && typeof v === 'object' && 'savedAt' in v ? (v as SavedEntry).savedAt : 0
      return { key: k, savedAt }
    })
    .sort((a, b) => a.savedAt - b.savedAt)

  if (candidates.length === 0) return false
  delete store[candidates[0].key]
  return true
}

function readStore(): StoredMap {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as StoredMap) : {}
  } catch {
    // Corrupted blob — drop it so the next save starts from a clean slate.
    localStorage.removeItem(STORAGE_KEY)
    return {}
  }
}

// Write the store, evicting oldest entries on quota errors and retrying.
// If even the current entry alone won't fit, give up silently — the
// server-side review session is the durable backup; localStorage is just
// a crash-recovery convenience and shouldn't break the UI.
function writeStore(store: StoredMap, protectedKey: string): void {
  for (let i = 0; i < 32; i++) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
      return
    } catch (e) {
      if (!isQuotaError(e)) throw e
      if (!evictOldest(store, protectedKey)) return
    }
  }
}

export const loadSavedData = (initialData: CorrectionData): CorrectionData | null => {
  const storageKey = generateStorageKey(initialData)
  const savedDataObj = readStore()

  if (savedDataObj[storageKey]) {
    try {
      const parsed = unwrap(savedDataObj[storageKey])
      // Compare first segment text instead of transcribed_text
      if (parsed && parsed.original_segments[0]?.text === initialData.original_segments[0]?.text) {
        const strippedSaved = stripIds(parsed)
        const strippedInitial = stripIds(initialData)
        const hasChanges = JSON.stringify(strippedSaved) !== JSON.stringify(strippedInitial)

        if (hasChanges) {
          return parsed
        } else {
          // Clean up storage if no changes
          delete savedDataObj[storageKey]
          writeStore(savedDataObj, storageKey)
        }
      }
    } catch (error) {
      console.error('Failed to parse saved data:', error)
      delete savedDataObj[storageKey]
      writeStore(savedDataObj, storageKey)
    }
  }
  return null
}

export const saveData = (data: CorrectionData, initialData: CorrectionData): void => {
  const storageKey = generateStorageKey(initialData)
  const savedDataObj = readStore()

  savedDataObj[storageKey] = { savedAt: Date.now(), data }
  writeStore(savedDataObj, storageKey)
}

export const clearSavedData = (data: CorrectionData): void => {
  const storageKey = generateStorageKey(data)
  const savedDataObj = readStore()

  delete savedDataObj[storageKey]
  writeStore(savedDataObj, storageKey)
}
