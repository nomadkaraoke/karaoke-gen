import { durationToCredits, isBlocked, formatDurationCost } from "@/lib/pricing";

describe("pricing", () => {
  it.each([
    [0, 1], [600, 1], [601, 2], [1200, 2], [3000, 5], [3001, 6], [3600, 6],
  ])("durationToCredits(%i) === %i", (seconds, expected) => {
    expect(durationToCredits(seconds)).toBe(expected);
  });

  it("blocks over 60 min", () => {
    expect(isBlocked(3600)).toBe(false);
    expect(isBlocked(3601)).toBe(true);
  });

  it("formats duration + cost", () => {
    expect(formatDurationCost(905)).toEqual({ minutes: 16, credits: 2 });
  });
});
