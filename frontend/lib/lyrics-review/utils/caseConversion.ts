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

function capitalizeFirstLetter(text: string): string {
  const firstLetter = text.search(/\p{L}/u)
  if (firstLetter === -1) return text
  return (
    text.slice(0, firstLetter) +
    text.charAt(firstLetter).toUpperCase() +
    text.slice(firstLetter + 1)
  )
}

function toTitleCase(text: string): string {
  return text.replace(/\S+/gu, (word) => capitalizeFirstLetter(word.toLowerCase()))
}

function toSentenceCase(text: string): string {
  return capitalizeFirstLetter(text.toLowerCase())
}

export function applyCaseToSegments(
  segments: LyricsSegment[],
  caseType: CaseType
): LyricsSegment[] {
  const perLine = caseType === 'sentence'
  return segments.map((segment) => {
    const transform = (s: string) => convertCase(s, caseType)
    // For sentence case, find the first word containing a Unicode letter — so a
    // leading punctuation-only token doesn't steal the capitalization.
    const firstCasedIndex = perLine
      ? segment.words.findIndex((w) => /\p{L}/u.test(w.text))
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
