/**
 * Tests for stringSimilarity and findFuzzyMatches pure functions.
 *
 * These are the core of the "Did you mean?" feature in AudioSourceStep.
 * stringSimilarity uses LCS-based similarity with a substring boost.
 * findFuzzyMatches uses stringSimilarity to rank catalog tracks against user input.
 */

import { stringSimilarity, findFuzzyMatches } from "../steps/AudioSourceStep"
import type { CatalogTrackResult } from "@/lib/api"

// ---------------------------------------------------------------------------
// stringSimilarity
// ---------------------------------------------------------------------------

describe("stringSimilarity", () => {
  it("returns 1 for identical strings", () => {
    expect(stringSimilarity("hello", "hello")).toBe(1)
  })

  it("returns 1 for case-insensitive identical strings", () => {
    expect(stringSimilarity("Hello", "hello")).toBe(1)
    expect(stringSimilarity("BOHEMIAN RHAPSODY", "bohemian rhapsody")).toBe(1)
  })

  it("returns 0 for empty string vs non-empty", () => {
    expect(stringSimilarity("", "hello")).toBe(0)
    expect(stringSimilarity("hello", "")).toBe(0)
  })

  it("returns 1 for both empty strings (handled by identity check)", () => {
    // Both empty => al === bl => returns 1
    expect(stringSimilarity("", "")).toBe(1)
  })

  it("returns 0.85 for substring match (a is substring of b)", () => {
    expect(stringSimilarity("water", "waterloo")).toBe(0.85)
  })

  it("returns 0.85 for substring match (b is substring of a)", () => {
    expect(stringSimilarity("waterloo", "water")).toBe(0.85)
  })

  it("does not trigger substring match for very short strings (< 3 chars)", () => {
    const score = stringSimilarity("ab", "abc")
    // "ab" length < 3, so no substring boost from first check
    // "abc" length >= 3 and "abc".includes("ab") is false (wrong direction)
    // Actually: al="ab", bl="abc". al.length >= 3 is false. bl.length >= 3 is true, al.includes(bl)="ab".includes("abc") is false.
    // So falls through to LCS. LCS of "ab" and "abc" = 2, max(2,3) = 3, score = 2*2/5 = 0.8
    expect(score).toBeGreaterThan(0)
    expect(score).toBeLessThan(0.85)
  })

  it("returns high score for similar strings", () => {
    const score = stringSimilarity("Bohemian Rhapsody", "Bohemian Rapsody")
    expect(score).toBeGreaterThan(0.8)
  })

  it("returns low score for very different strings", () => {
    const score = stringSimilarity("ABBA", "Metallica")
    expect(score).toBeLessThan(0.5)
  })

  it("handles single character strings", () => {
    const score = stringSimilarity("a", "b")
    expect(score).toBe(0)
  })

  it("handles single character match", () => {
    const score = stringSimilarity("a", "a")
    expect(score).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// findFuzzyMatches
// ---------------------------------------------------------------------------

function makeTracks(names: Array<[string, string]>): CatalogTrackResult[] {
  return names.map(([artist, track]) => ({
    artist_name: artist,
    track_name: track,
  }))
}

describe("findFuzzyMatches", () => {
  const tracks = makeTracks([
    ["ABBA", "Waterloo"],
    ["ABBA", "Dancing Queen"],
    ["ABBA", "Mamma Mia"],
    ["Queen", "Bohemian Rhapsody"],
    ["The Beatles", "Hey Jude"],
  ])

  it("returns empty array when no tracks match above threshold", () => {
    const result = findFuzzyMatches("zzzzzzzzzzzzz", tracks)
    expect(result).toEqual([])
  })

  it("finds fuzzy match for misspelled title", () => {
    const result = findFuzzyMatches("Watrloo", tracks)
    expect(result.length).toBeGreaterThan(0)
    expect(result[0].track_name).toBe("Waterloo")
  })

  it("excludes exact title match by default", () => {
    const result = findFuzzyMatches("Waterloo", tracks)
    // "Waterloo" exactly matches a track — should be excluded from fuzzy results
    expect(result.every(t => t.track_name.toLowerCase() !== "waterloo")).toBe(true)
  })

  it("includes exact title match when allowExactTitle is true", () => {
    const result = findFuzzyMatches("Waterloo", tracks, 0.6, true)
    expect(result.some(t => t.track_name === "Waterloo")).toBe(true)
  })

  it("respects threshold parameter", () => {
    // With a very high threshold, only very close matches pass
    const strict = findFuzzyMatches("Watrloo", tracks, 0.99)
    expect(strict).toEqual([])

    // With a low threshold, more matches pass
    const lenient = findFuzzyMatches("Watrloo", tracks, 0.3)
    expect(lenient.length).toBeGreaterThan(0)
  })

  it("applies artist bonus when userArtist is provided", () => {
    // Two tracks with similar titles but different artists
    const tracksWithSimilar = makeTracks([
      ["ABBA", "Love Story"],
      ["Taylor Swift", "Love Story"],
    ])
    const result = findFuzzyMatches("Love Storry", tracksWithSimilar, 0.6, false, "Taylor Swift")
    // Taylor Swift version should rank higher due to artist bonus
    if (result.length >= 2) {
      expect(result[0].artist_name).toBe("Taylor Swift")
    }
  })

  it("deduplicates results by artist+track name", () => {
    const tracksWithDupes = makeTracks([
      ["ABBA", "Waterloo"],
      ["ABBA", "Waterloo"],
      ["ABBA", "Waterloo"],
    ])
    const result = findFuzzyMatches("Watrloo", tracksWithDupes, 0.5)
    expect(result.length).toBeLessThanOrEqual(1)
  })

  it("returns results sorted by score descending", () => {
    // Create tracks with varying similarity to "Waterlo"
    const tracksToSort = makeTracks([
      ["ABBA", "Waterloo"],       // Very close
      ["ABBA", "Water"],          // Less close
      ["ABBA", "Waterfalls"],     // Medium
    ])
    const result = findFuzzyMatches("Waterlo", tracksToSort, 0.3, true)
    // The closest match should come first
    if (result.length >= 2) {
      expect(result[0].track_name).toBe("Waterloo")
    }
  })

  it("handles empty tracks array", () => {
    const result = findFuzzyMatches("Waterloo", [])
    expect(result).toEqual([])
  })

  it("handles empty user title", () => {
    const result = findFuzzyMatches("", tracks)
    expect(result).toEqual([])
  })
})
