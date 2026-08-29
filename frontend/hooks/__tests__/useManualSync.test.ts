import { clampSyncTime } from '../useManualSync'

describe('clampSyncTime', () => {
  it('clamps a playhead-at-0 tap up to the segment start', () => {
    expect(clampSyncTime(0, 15.18, 18.04)).toBe(15.18)
  })
  it('clamps a tap past the segment end down to the end', () => {
    expect(clampSyncTime(99, 15.18, 18.04)).toBe(18.04)
  })
  it('leaves an in-window tap unchanged', () => {
    expect(clampSyncTime(16.0, 15.18, 18.04)).toBe(16.0)
  })
  it('falls back gracefully when segment bounds are null', () => {
    expect(clampSyncTime(5, null, null)).toBe(5)
  })

  describe('with a lead-in window (playback starts before the segment)', () => {
    it('keeps a legitimate tap in the lead-in instead of shoving it to segStart', () => {
      // Regression: re-syncing "Well," — tapped at 0.468, segStart had ratcheted to 0.688.
      // Old behaviour clamped up to 0.688 and squished the word; now it's kept.
      expect(clampSyncTime(0.468, 0.688, 4.48, 3)).toBe(0.468)
    })
    it('keeps a tap a second before a mid-song segment start', () => {
      expect(clampSyncTime(14.0, 15.18, 18.04, 3)).toBe(14.0)
    })
    it('still clamps a genuine playhead-at-0 glitch up to (segStart - leadIn)', () => {
      expect(clampSyncTime(0, 15.18, 18.04, 3)).toBeCloseTo(12.18, 5)
    })
    it('floors the lower bound at 0 for segments near the song start', () => {
      expect(clampSyncTime(0, 0.688, 4.48, 3)).toBe(0)
    })
    it('clamps a far-future glitch to the segment end when no lead-out is given', () => {
      expect(clampSyncTime(99, 15.18, 18.04, 3)).toBe(18.04)
    })
    it('keeps a tap just past the segment end within the lead-out (extends the segment)', () => {
      expect(clampSyncTime(18.5, 15.18, 18.04, 3, 3)).toBe(18.5)
    })
    it('still clamps a far-future glitch to (segEnd + leadOut)', () => {
      expect(clampSyncTime(99, 15.18, 18.04, 3, 3)).toBeCloseTo(21.04, 5)
    })
  })
})
