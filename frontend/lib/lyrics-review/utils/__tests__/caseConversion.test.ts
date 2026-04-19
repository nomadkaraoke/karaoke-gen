import { convertCase, applyCaseToSegments } from '../caseConversion'
import type { LyricsSegment } from '../../types'

describe('convertCase', () => {
  describe('upper', () => {
    it('uppercases all letters', () => {
      expect(convertCase('Hello World', 'upper')).toBe('HELLO WORLD')
    })

    it('preserves punctuation and spacing', () => {
      expect(convertCase("don't stop, believin'", 'upper')).toBe("DON'T STOP, BELIEVIN'")
    })

    it('handles empty string', () => {
      expect(convertCase('', 'upper')).toBe('')
    })
  })

  describe('lower', () => {
    it('lowercases all letters', () => {
      expect(convertCase('HELLO WORLD', 'lower')).toBe('hello world')
    })

    it('handles mixed case', () => {
      expect(convertCase('HeLLo WoRLD', 'lower')).toBe('hello world')
    })
  })

  describe('title', () => {
    it('capitalizes first letter of every word', () => {
      expect(convertCase('hello world', 'title')).toBe('Hello World')
    })

    it('lowercases subsequent letters', () => {
      expect(convertCase('HELLO WORLD', 'title')).toBe('Hello World')
    })

    it('handles short words uniformly (no AP-style exceptions)', () => {
      expect(convertCase('the quick brown fox', 'title')).toBe('The Quick Brown Fox')
    })

    it('handles contractions by capitalizing only first letter', () => {
      expect(convertCase("don't stop", 'title')).toBe("Don't Stop")
    })

    it('preserves multiple spaces between words', () => {
      expect(convertCase('hello  world', 'title')).toBe('Hello  World')
    })
  })

  describe('sentence', () => {
    it('capitalizes first letter only', () => {
      expect(convertCase('hello world', 'sentence')).toBe('Hello world')
    })

    it('lowercases everything else', () => {
      expect(convertCase('HELLO WORLD HOW ARE YOU', 'sentence')).toBe('Hello world how are you')
    })

    it('skips leading punctuation when capitalizing', () => {
      expect(convertCase('"hello world"', 'sentence')).toBe('"Hello world"')
    })

    it('handles string with no letters', () => {
      expect(convertCase('123 !@#', 'sentence')).toBe('123 !@#')
    })
  })
})

describe('applyCaseToSegments', () => {
  const segments: LyricsSegment[] = [
    {
      id: 'seg-1',
      text: 'Hello world',
      start_time: 0,
      end_time: 2,
      words: [
        { id: 'w1', text: 'Hello', start_time: 0, end_time: 1, confidence: 1 },
        { id: 'w2', text: 'world', start_time: 1, end_time: 2, confidence: 1 },
      ],
    },
    {
      id: 'seg-2',
      text: 'How are YOU',
      start_time: 2,
      end_time: 5,
      words: [
        { id: 'w3', text: 'How', start_time: 2, end_time: 3, confidence: 1 },
        { id: 'w4', text: 'are', start_time: 3, end_time: 4, confidence: 1 },
        { id: 'w5', text: 'YOU', start_time: 4, end_time: 5, confidence: 1 },
      ],
    },
  ]

  it('preserves word timing and IDs', () => {
    const result = applyCaseToSegments(segments, 'upper')
    expect(result[0].words[0].id).toBe('w1')
    expect(result[0].words[0].start_time).toBe(0)
    expect(result[0].words[0].end_time).toBe(1)
    expect(result[0].start_time).toBe(0)
    expect(result[0].end_time).toBe(2)
  })

  it('uppercases all text', () => {
    const result = applyCaseToSegments(segments, 'upper')
    expect(result[0].text).toBe('HELLO WORLD')
    expect(result[0].words.map((w) => w.text)).toEqual(['HELLO', 'WORLD'])
    expect(result[1].text).toBe('HOW ARE YOU')
  })

  it('lowercases all text', () => {
    const result = applyCaseToSegments(segments, 'lower')
    expect(result[0].text).toBe('hello world')
    expect(result[1].words.map((w) => w.text)).toEqual(['how', 'are', 'you'])
  })

  it('title-cases all words', () => {
    const result = applyCaseToSegments(segments, 'title')
    expect(result[1].text).toBe('How Are You')
    expect(result[1].words.map((w) => w.text)).toEqual(['How', 'Are', 'You'])
  })

  it('sentence-cases per line (only first word capitalized)', () => {
    const result = applyCaseToSegments(segments, 'sentence')
    expect(result[0].text).toBe('Hello world')
    expect(result[0].words.map((w) => w.text)).toEqual(['Hello', 'world'])
    expect(result[1].text).toBe('How are you')
    expect(result[1].words.map((w) => w.text)).toEqual(['How', 'are', 'you'])
  })

  it('does not mutate original segments', () => {
    const original = JSON.parse(JSON.stringify(segments))
    applyCaseToSegments(segments, 'upper')
    expect(segments).toEqual(original)
  })

  it('sentence-cases the first cased word even when a punctuation-only token precedes it', () => {
    const punctSegments: LyricsSegment[] = [
      {
        id: 'seg-p',
        text: '"hello there"',
        start_time: 0,
        end_time: 2,
        words: [
          { id: 'p1', text: '"', start_time: 0, end_time: 0.1, confidence: 1 },
          { id: 'p2', text: 'hello', start_time: 0.1, end_time: 1, confidence: 1 },
          { id: 'p3', text: 'there"', start_time: 1, end_time: 2, confidence: 1 },
        ],
      },
    ]
    const result = applyCaseToSegments(punctSegments, 'sentence')
    expect(result[0].words[0].text).toBe('"')
    expect(result[0].words[1].text).toBe('Hello')
    expect(result[0].words[2].text).toBe('there"')
  })
})
