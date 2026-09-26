import { cumulativeTempoFactor, tempoPercent, isTempoAdjusted, tempoLabel } from "../tempo"

describe("tempo helpers", () => {
  it("multiplies tempo edits and ignores other operations", () => {
    expect(cumulativeTempoFactor([])).toBe(1)
    expect(
      cumulativeTempoFactor([
        { operation: "trim_start", params: { end_seconds: 5 } },
        { operation: "tempo", params: { factor: 0.9 } },
        { operation: "tempo", params: { factor: 0.9 } },
      ]),
    ).toBeCloseTo(0.81)
  })

  it("skips malformed tempo entries", () => {
    expect(
      cumulativeTempoFactor([
        { operation: "tempo", params: {} },
        { operation: "tempo", params: { factor: "x" } },
        { operation: "tempo", params: { factor: -2 } },
        { operation: "tempo", params: { factor: 1.2 } },
      ]),
    ).toBeCloseTo(1.2)
  })

  it("rounds to whole percent and treats ~100% as unadjusted", () => {
    expect(tempoPercent(0.849)).toBe(85)
    expect(isTempoAdjusted(0.9)).toBe(true)
    expect(isTempoAdjusted(0.9 * 1.111)).toBe(false)
    expect(isTempoAdjusted(1)).toBe(false)
    expect(isTempoAdjusted(null)).toBe(false)
    expect(isTempoAdjusted(undefined)).toBe(false)
  })

  // Must match backend tempo_label.tempo_percent (half-up) — see TestRoundingParityWithFrontend
  it.each([
    [[0.9, 1.05], 95],
    [[0.85, 0.9], 77],
    [[0.95, 1.1], 105],
  ])("labels compound presets %p as %p%%", (factors, expected) => {
    const factor = cumulativeTempoFactor(
      (factors as number[]).map((f) => ({ operation: "tempo", params: { factor: f } })),
    )
    expect(tempoLabel(factor)).toBe(`(${expected}% Tempo)`)
  })
})
