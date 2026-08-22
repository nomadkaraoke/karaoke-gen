/**
 * Conflict-group helpers for multi-model auto-correct suggestions.
 *
 * Suggestions sharing a conflict_group target overlapping words with
 * different outcomes; the reviewer accepts at most one per group.
 */
import type { AiSuggestion } from '@/lib/api/autoCorrect'

/** Other suggestions in the same conflict group as `id`. */
export function getConflictSiblings(
  suggestions: AiSuggestion[],
  id: string,
): AiSuggestion[] {
  const target = suggestions.find((s) => s.id === id)
  if (!target?.conflict_group) return []
  return suggestions.filter(
    (s) => s.id !== id && s.conflict_group === target.conflict_group,
  )
}

/**
 * For accept-all: pick the winner of each conflict group (highest model
 * consensus, then highest confidence); non-conflicting suggestions all win.
 * Returns ids in the original suggestion order.
 */
export function pickAcceptAllWinners(suggestions: AiSuggestion[]): string[] {
  const bestOfGroup = new Map<string, AiSuggestion>()
  for (const s of suggestions) {
    if (!s.conflict_group) continue
    const current = bestOfGroup.get(s.conflict_group)
    if (
      !current ||
      s.consensus > current.consensus ||
      (s.consensus === current.consensus && s.confidence > current.confidence)
    ) {
      bestOfGroup.set(s.conflict_group, s)
    }
  }
  return suggestions
    .filter((s) => !s.conflict_group || bestOfGroup.get(s.conflict_group)?.id === s.id)
    .map((s) => s.id)
}
