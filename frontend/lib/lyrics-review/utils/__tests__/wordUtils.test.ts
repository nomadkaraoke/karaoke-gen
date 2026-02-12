import { splitWordWithTiming, createWordsWithDistributedTiming, findWordById, getWordsFromIds } from '../wordUtils'
import { LyricsSegment } from '../../types'

describe('splitWordWithTiming', () => {
  describe('timing distribution', () => {
    it('distributes timing equally when splitting text with whitespace', () => {
      const result = splitWordWithTiming('hello world', 1.0, 2.0)

      expect(result).toHaveLength(2)
      expect(result[0]).toEqual({ text: 'hello', start_time: 1.0, end_time: 1.5 })
      expect(result[1]).toEqual({ text: 'world', start_time: 1.5, end_time: 2.0 })
    })

    it('distributes timing equally when splitting single word in half', () => {
      const result = splitWordWithTiming('testing', 0.0, 1.0)

      expect(result).toHaveLength(2)
      // 'testing' (7 chars) splits to 'test' (4 chars) and 'ing' (3 chars)
      expect(result[0]).toEqual({ text: 'test', start_time: 0.0, end_time: 0.5 })
      expect(result[1]).toEqual({ text: 'ing', start_time: 0.5, end_time: 1.0 })
    })

    it('handles three-word split correctly', () => {
      const result = splitWordWithTiming('one two three', 0.0, 3.0)

      expect(result).toHaveLength(3)
      expect(result[0]).toEqual({ text: 'one', start_time: 0.0, end_time: 1.0 })
      expect(result[1]).toEqual({ text: 'two', start_time: 1.0, end_time: 2.0 })
      expect(result[2]).toEqual({ text: 'three', start_time: 2.0, end_time: 3.0 })
    })

    it('handles four-word split correctly', () => {
      const result = splitWordWithTiming('a b c d', 0.0, 4.0)

      expect(result).toHaveLength(4)
      expect(result[0]).toEqual({ text: 'a', start_time: 0.0, end_time: 1.0 })
      expect(result[1]).toEqual({ text: 'b', start_time: 1.0, end_time: 2.0 })
      expect(result[2]).toEqual({ text: 'c', start_time: 2.0, end_time: 3.0 })
      expect(result[3]).toEqual({ text: 'd', start_time: 3.0, end_time: 4.0 })
    })

    it('preserves exact timing boundaries (no gaps or overlaps)', () => {
      const result = splitWordWithTiming('word1 word2 word3', 5.5, 8.5)

      // Total duration: 3.0 seconds, split 3 ways = 1.0 second each
      expect(result[0].start_time).toBe(5.5)
      expect(result[0].end_time).toBe(6.5)
      expect(result[1].start_time).toBe(6.5) // No gap
      expect(result[1].end_time).toBe(7.5)
      expect(result[2].start_time).toBe(7.5) // No gap
      expect(result[2].end_time).toBe(8.5) // Matches original end
    })
  })

  describe('null timing handling', () => {
    it('returns null timing when start_time is null', () => {
      const result = splitWordWithTiming('hello world', null, 2.0)

      expect(result).toHaveLength(2)
      expect(result[0]).toEqual({ text: 'hello', start_time: null, end_time: null })
      expect(result[1]).toEqual({ text: 'world', start_time: null, end_time: null })
    })

    it('returns null timing when end_time is null', () => {
      const result = splitWordWithTiming('hello world', 1.0, null)

      expect(result).toHaveLength(2)
      expect(result[0]).toEqual({ text: 'hello', start_time: null, end_time: null })
      expect(result[1]).toEqual({ text: 'world', start_time: null, end_time: null })
    })

    it('returns null timing when both times are null', () => {
      const result = splitWordWithTiming('hello world', null, null)

      expect(result).toHaveLength(2)
      expect(result[0]).toEqual({ text: 'hello', start_time: null, end_time: null })
      expect(result[1]).toEqual({ text: 'world', start_time: null, end_time: null })
    })
  })

  describe('text splitting behavior', () => {
    it('splits by whitespace', () => {
      const result = splitWordWithTiming('split  by   whitespace', 0, 3)

      expect(result).toHaveLength(3)
      expect(result.map((r) => r.text)).toEqual(['split', 'by', 'whitespace'])
    })

    it('splits single word in half (ceiling for first half)', () => {
      // Odd number of characters: 'abc' (3 chars) -> 'ab' (2) + 'c' (1)
      const result = splitWordWithTiming('abc', 0, 2)

      expect(result).toHaveLength(2)
      expect(result[0].text).toBe('ab')
      expect(result[1].text).toBe('c')
    })

    it('splits even-length single word evenly', () => {
      // Even number: 'abcd' (4 chars) -> 'ab' (2) + 'cd' (2)
      const result = splitWordWithTiming('abcd', 0, 2)

      expect(result).toHaveLength(2)
      expect(result[0].text).toBe('ab')
      expect(result[1].text).toBe('cd')
    })

    it('handles tabs and newlines as whitespace', () => {
      const result = splitWordWithTiming("word1\tword2\nword3", 0, 3)

      expect(result).toHaveLength(3)
      expect(result.map((r) => r.text)).toEqual(['word1', 'word2', 'word3'])
    })
  })

  describe('edge cases', () => {
    it('handles zero-duration timing', () => {
      const result = splitWordWithTiming('hello world', 5.0, 5.0)

      expect(result).toHaveLength(2)
      expect(result[0]).toEqual({ text: 'hello', start_time: 5.0, end_time: 5.0 })
      expect(result[1]).toEqual({ text: 'world', start_time: 5.0, end_time: 5.0 })
    })

    it('handles very small durations with precision', () => {
      const result = splitWordWithTiming('hello world', 1.0, 1.1)

      expect(result).toHaveLength(2)
      expect(result[0].start_time).toBe(1.0)
      expect(result[0].end_time).toBeCloseTo(1.05, 10)
      expect(result[1].start_time).toBeCloseTo(1.05, 10)
      expect(result[1].end_time).toBe(1.1)
    })

    it('handles single character word', () => {
      const result = splitWordWithTiming('x', 0, 1)

      // Single char 'x': Math.ceil(1/2) = 1, so slice(0,1)='x' and slice(1)=''
      // Then filter removes empty strings, leaving just ['x']
      expect(result).toHaveLength(1)
      expect(result[0].text).toBe('x')
    })

    it('handles two character word', () => {
      const result = splitWordWithTiming('hi', 0, 2)

      expect(result).toHaveLength(2)
      expect(result[0].text).toBe('h')
      expect(result[1].text).toBe('i')
    })

    it('handles empty string input', () => {
      const result = splitWordWithTiming('', 0, 1)

      expect(result).toHaveLength(0)
    })

    it('handles whitespace-only input', () => {
      const result = splitWordWithTiming('   ', 0, 1)

      expect(result).toHaveLength(0)
    })
  })
})

describe('createWordsWithDistributedTiming', () => {
  it('distributes timing equally for 3 words across 0-3s', () => {
    const result = createWordsWithDistributedTiming('one two three', 0, 3)

    expect(result).toHaveLength(3)
    expect(result[0]).toMatchObject({ text: 'one', start_time: 0, end_time: 1, confidence: 1.0 })
    expect(result[1]).toMatchObject({ text: 'two', start_time: 1, end_time: 2, confidence: 1.0 })
    expect(result[2]).toMatchObject({ text: 'three', start_time: 2, end_time: 3, confidence: 1.0 })
  })

  it('single word fills entire segment', () => {
    const result = createWordsWithDistributedTiming('hello', 5, 8)

    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ text: 'hello', start_time: 5, end_time: 8 })
  })

  it('returns empty array for empty text', () => {
    const result = createWordsWithDistributedTiming('', 0, 3)
    expect(result).toHaveLength(0)
  })

  it('returns empty array for whitespace-only text', () => {
    const result = createWordsWithDistributedTiming('   \t  ', 0, 3)
    expect(result).toHaveLength(0)
  })

  it('defaults to 0-1s when timing is null', () => {
    const result = createWordsWithDistributedTiming('hello world', null, null)

    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({ text: 'hello', start_time: 0, end_time: 0.5 })
    expect(result[1]).toMatchObject({ text: 'world', start_time: 0.5, end_time: 1 })
  })

  it('defaults start to 0 and end to 1 when only start is null', () => {
    const result = createWordsWithDistributedTiming('a b', null, null)

    expect(result[0].start_time).toBe(0)
    expect(result[1].end_time).toBe(1)
  })

  it('generates unique IDs for each word', () => {
    const result = createWordsWithDistributedTiming('a b c', 0, 3)
    const ids = result.map((w) => w.id)
    expect(new Set(ids).size).toBe(3)
  })

  it('handles extra whitespace between words', () => {
    const result = createWordsWithDistributedTiming('  hello   world  ', 0, 2)

    expect(result).toHaveLength(2)
    expect(result[0].text).toBe('hello')
    expect(result[1].text).toBe('world')
  })
})

describe('findWordById', () => {
  const mockSegments: LyricsSegment[] = [
    {
      id: 'seg1',
      text: 'Hello world',
      start_time: 0,
      end_time: 2,
      words: [
        { id: 'word1', text: 'Hello', start_time: 0, end_time: 1, confidence: 0.9 },
        { id: 'word2', text: 'world', start_time: 1, end_time: 2, confidence: 0.95 },
      ],
    },
    {
      id: 'seg2',
      text: 'Test segment',
      start_time: 3,
      end_time: 5,
      words: [
        { id: 'word3', text: 'Test', start_time: 3, end_time: 4, confidence: 0.92 },
        { id: 'word4', text: 'segment', start_time: 4, end_time: 5, confidence: 0.88 },
      ],
    },
  ]

  it('finds word in first segment', () => {
    const word = findWordById(mockSegments, 'word1')
    expect(word).toEqual({ id: 'word1', text: 'Hello', start_time: 0, end_time: 1, confidence: 0.9 })
  })

  it('finds word in second segment', () => {
    const word = findWordById(mockSegments, 'word4')
    expect(word?.text).toBe('segment')
  })

  it('returns undefined for non-existent word', () => {
    const word = findWordById(mockSegments, 'nonexistent')
    expect(word).toBeUndefined()
  })

  it('returns undefined for empty segments', () => {
    const word = findWordById([], 'word1')
    expect(word).toBeUndefined()
  })
})

describe('getWordsFromIds', () => {
  const mockSegments: LyricsSegment[] = [
    {
      id: 'seg1',
      text: 'Hello world',
      start_time: 0,
      end_time: 2,
      words: [
        { id: 'word1', text: 'Hello', start_time: 0, end_time: 1, confidence: 0.9 },
        { id: 'word2', text: 'world', start_time: 1, end_time: 2, confidence: 0.95 },
      ],
    },
  ]

  it('returns words for valid IDs', () => {
    const words = getWordsFromIds(mockSegments, ['word1', 'word2'])
    expect(words).toHaveLength(2)
    expect(words[0].text).toBe('Hello')
    expect(words[1].text).toBe('world')
  })

  it('filters out invalid IDs', () => {
    const words = getWordsFromIds(mockSegments, ['word1', 'invalid', 'word2'])
    expect(words).toHaveLength(2)
  })

  it('returns empty array for all invalid IDs', () => {
    const words = getWordsFromIds(mockSegments, ['invalid1', 'invalid2'])
    expect(words).toHaveLength(0)
  })

  it('returns empty array for empty ID list', () => {
    const words = getWordsFromIds(mockSegments, [])
    expect(words).toHaveLength(0)
  })
})
