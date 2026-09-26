/**
 * Tempo-adjustment helpers shared by the audio editor and job UI.
 *
 * Mirrors backend/services/tempo_label.py — keep the two in sync so the label a
 * user sees in the editor is exactly what lands in the published outputs.
 */

export const MIN_TEMPO_PERCENT = 50
export const MAX_TEMPO_PERCENT = 150

interface TempoEdit {
  operation: string
  params: Record<string, unknown>
}

/** Cumulative tempo factor from an audio edit stack (1 = original speed). */
export function cumulativeTempoFactor(editStack: TempoEdit[]): number {
  return editStack.reduce((acc, edit) => {
    if (edit.operation !== "tempo") return acc
    const factor = Number(edit.params?.factor)
    return Number.isFinite(factor) && factor > 0 ? acc * factor : acc
  }, 1)
}

/** Tempo as a whole percentage of the original (e.g. 0.9 -> 90). */
export function tempoPercent(factor: number): number {
  return Math.round(factor * 100)
}

/** Whether a tempo factor is far enough from 1 to be labeled (rounds to != 100%). */
export function isTempoAdjusted(factor: number | null | undefined): boolean {
  return factor != null && Number.isFinite(factor) && tempoPercent(factor) !== 100
}

/**
 * The label appended to the title of tempo-changed tracks in every published
 * output. Deliberately not translated: it's what actually appears in filenames,
 * title screens and YouTube titles (backend/services/tempo_label.py#tempo_suffix).
 */
export function tempoLabel(factor: number): string {
  return `(${tempoPercent(factor)}% Tempo)`
}
