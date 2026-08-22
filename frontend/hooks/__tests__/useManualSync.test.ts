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
})
