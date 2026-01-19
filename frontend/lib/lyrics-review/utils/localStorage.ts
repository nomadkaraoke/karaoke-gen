import { CorrectionData, LyricsSegment } from '../types'

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

export const loadSavedData = (initialData: CorrectionData): CorrectionData | null => {
  const storageKey = generateStorageKey(initialData)
  const savedDataStr = localStorage.getItem('lyrics_analyzer_data')
  const savedDataObj = savedDataStr ? JSON.parse(savedDataStr) : {}

  if (savedDataObj[storageKey]) {
    try {
      const parsed = savedDataObj[storageKey]
      // Compare first segment text instead of transcribed_text
      if (parsed.original_segments[0]?.text === initialData.original_segments[0]?.text) {
        const strippedSaved = stripIds(parsed)
        const strippedInitial = stripIds(initialData)
        const hasChanges = JSON.stringify(strippedSaved) !== JSON.stringify(strippedInitial)

        if (hasChanges) {
          return parsed
        } else {
          // Clean up storage if no changes
          delete savedDataObj[storageKey]
          localStorage.setItem('lyrics_analyzer_data', JSON.stringify(savedDataObj))
        }
      }
    } catch (error) {
      console.error('Failed to parse saved data:', error)
      delete savedDataObj[storageKey]
      localStorage.setItem('lyrics_analyzer_data', JSON.stringify(savedDataObj))
    }
  }
  return null
}

export const saveData = (data: CorrectionData, initialData: CorrectionData): void => {
  const storageKey = generateStorageKey(initialData)
  const savedDataStr = localStorage.getItem('lyrics_analyzer_data')
  const savedDataObj = savedDataStr ? JSON.parse(savedDataStr) : {}

  savedDataObj[storageKey] = data
  localStorage.setItem('lyrics_analyzer_data', JSON.stringify(savedDataObj))
}

export const clearSavedData = (data: CorrectionData): void => {
  const storageKey = generateStorageKey(data)
  const savedDataStr = localStorage.getItem('lyrics_analyzer_data')
  const savedDataObj = savedDataStr ? JSON.parse(savedDataStr) : {}

  delete savedDataObj[storageKey]
  localStorage.setItem('lyrics_analyzer_data', JSON.stringify(savedDataObj))
}
