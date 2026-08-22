import { parseServerDate } from '../utils'

describe('parseServerDate', () => {
  it('treats an offset-less ISO timestamp (naive UTC) as UTC', () => {
    // Backend serializes datetime.utcnow() with no timezone designator.
    // This should be interpreted as UTC, NOT local time.
    const d = parseServerDate('2026-08-14T18:10:25')
    expect(d.getTime()).toBe(Date.UTC(2026, 7, 14, 18, 10, 25))
  })

  it('treats an offset-less timestamp with fractional seconds as UTC', () => {
    const d = parseServerDate('2026-08-14T18:10:25.123456')
    expect(d.getTime()).toBe(Date.UTC(2026, 7, 14, 18, 10, 25, 123))
  })

  it('respects an explicit Z (UTC) designator', () => {
    const d = parseServerDate('2026-08-14T18:10:25Z')
    expect(d.getTime()).toBe(Date.UTC(2026, 7, 14, 18, 10, 25))
  })

  it('respects an explicit +00:00 offset', () => {
    const d = parseServerDate('2026-08-14T18:10:25+00:00')
    expect(d.getTime()).toBe(Date.UTC(2026, 7, 14, 18, 10, 25))
  })

  it('respects a non-UTC offset', () => {
    // 18:10 at +02:00 == 16:10 UTC
    const d = parseServerDate('2026-08-14T18:10:25+02:00')
    expect(d.getTime()).toBe(Date.UTC(2026, 7, 14, 16, 10, 25))
  })

  it('passes through Date instances unchanged', () => {
    const original = new Date(Date.UTC(2026, 7, 14, 18, 10, 25))
    expect(parseServerDate(original)).toBe(original)
  })

  it('accepts epoch milliseconds', () => {
    const ms = Date.UTC(2026, 7, 14, 18, 10, 25)
    expect(parseServerDate(ms).getTime()).toBe(ms)
  })

  it('does not append Z to non-ISO-datetime strings (date-only)', () => {
    // Date-only strings are already parsed as UTC by the Date constructor;
    // appending Z would be invalid. Ensure we leave them alone.
    const d = parseServerDate('2026-08-14')
    expect(d.getTime()).toBe(Date.UTC(2026, 7, 14))
  })
})
