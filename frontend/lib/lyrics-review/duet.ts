import type { LyricsSegment, SingerId } from './types'

/** Cycle through singer values: undefined/1 → 2 → 0 (Both) → 1. */
export function cycleSinger(current: SingerId | undefined): SingerId {
  const effective = current ?? 1
  if (effective === 1) return 2
  if (effective === 2) return 0
  return 1
}

/** The effective singer for a segment — defaults to 1 when unset. */
export function resolveSegmentSinger(segment: LyricsSegment): SingerId {
  return segment.singer ?? 1
}

/** All singer ids used in a list of segments, sorted with 1 first, then 2, then 0 (Both). */
export function collectSingersInUse(segments: LyricsSegment[]): SingerId[] {
  const seen = new Set<SingerId>([1])
  for (const seg of segments) {
    if (seg.singer !== undefined) seen.add(seg.singer)
    for (const w of seg.words) {
      if (w.singer !== undefined) seen.add(w.singer)
    }
  }
  const order: SingerId[] = [1, 2, 0]
  return order.filter(sid => seen.has(sid))
}

/** True when any word in the segment has a singer that differs from the resolved segment singer. */
export function hasWordOverrides(segment: LyricsSegment): boolean {
  const segmentSinger = resolveSegmentSinger(segment)
  return segment.words.some(w => w.singer !== undefined && w.singer !== segmentSinger)
}
