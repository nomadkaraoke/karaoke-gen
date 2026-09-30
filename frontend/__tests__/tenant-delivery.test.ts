import { deliveryError, suggestDelivery } from '@/lib/tenant-delivery'

describe('suggestDelivery', () => {
  it.each([
    ['Vocal Star', '/MediaUnsynced/Karaoke/Tracks-VocalStar', 'VSTAR'],
    ['Singa', '/MediaUnsynced/Karaoke/Tracks-Singa', 'SINGA'],
    ['Randy Vild', '/MediaUnsynced/Karaoke/Tracks-RandyVild', 'RVILD'],
  ])('matches the existing convention for %s', (name, path, prefix) => {
    expect(suggestDelivery(name)).toEqual({ dropbox_path: path, brand_prefix: prefix })
  })

  it('returns blanks for an empty name', () => {
    expect(suggestDelivery('  ')).toEqual({ dropbox_path: '', brand_prefix: '' })
  })

  it('leaves the prefix blank rather than suggest an invalid 1-char one', () => {
    expect(suggestDelivery('X').brand_prefix).toBe('')
  })

  it('caps the prefix at 8 chars and never starts with a digit', () => {
    expect(suggestDelivery('Supercalifragilistic').brand_prefix).toBe('SUPERCAL')
    expect(suggestDelivery('99 Luftballons').brand_prefix).toBe('LUFTBALL')
  })
})

describe('deliveryError', () => {
  it('allows both set or both blank', () => {
    expect(deliveryError('/K/T', 'ABC')).toBeNull()
    expect(deliveryError('', ' ')).toBeNull()
  })

  it('rejects one without the other', () => {
    expect(deliveryError('/K/T', '')).toMatch(/both/)
    expect(deliveryError('', 'ABC')).toMatch(/both/)
  })
})
