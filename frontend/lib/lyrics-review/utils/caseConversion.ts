import type { LyricsSegment } from '../types'

export type CaseType = 'upper' | 'lower' | 'title' | 'sentence'

export function convertCase(text: string, caseType: CaseType): string {
  switch (caseType) {
    case 'upper':
      return text.toUpperCase()
    case 'lower':
      return text.toLowerCase()
    case 'title':
      return toTitleCase(text)
    case 'sentence':
      return toSentenceCase(text)
  }
}

function toTitleCase(text: string): string {
  return text.replace(/\S+/g, (word) => {
    const lower = word.toLowerCase()
    return lower.charAt(0).toUpperCase() + lower.slice(1)
  })
}

function toSentenceCase(text: string): string {
  const lower = text.toLowerCase()
  const firstLetter = lower.search(/[a-z]/i)
  if (firstLetter === -1) return lower
  return lower.slice(0, firstLetter) + lower.charAt(firstLetter).toUpperCase() + lower.slice(firstLetter + 1)
}

export function applyCaseToSegments(
  segments: LyricsSegment[],
  caseType: CaseType
): LyricsSegment[] {
  const perLine = caseType === 'sentence'
  return segments.map((segment) => {
    const transform = (s: string) => convertCase(s, caseType)
    // For sentence case, find the first word containing a cased letter — so a
    // leading punctuation-only token doesn't steal the capitalization.
    const firstCasedIndex = perLine
      ? segment.words.findIndex((w) => /[A-Za-z]/.test(w.text))
      : -1
    const newWords = segment.words.map((word, index) => ({
      ...word,
      text: perLine
        ? index === firstCasedIndex
          ? convertCase(word.text, 'sentence')
          : word.text.toLowerCase()
        : transform(word.text),
    }))
    return {
      ...segment,
      text: transform(segment.text),
      words: newWords,
    }
  })
}
