/**
 * The local stack's fake audio search (backend/services/fake_workers/search.py)
 * shapes its results by the `search_confidence` fake-config knob so web and
 * mobile tests can reach each confidence tier. This feeds those exact shapes
 * (fixtures/fake-search-tiers.json, kept current by a backend test) to the real
 * ranking, so the fake and getSearchConfidence can't drift apart.
 *
 * Regenerate the fixture: poetry run python -m backend.services.fake_workers.search --write-fixture
 */
import fixture from './fixtures/fake-search-tiers.json'
import { getSearchConfidence, ExtendedAudioSearchResult } from '../audio-search-utils'

const shapes = fixture.shapes as unknown as Record<string, ExtendedAudioSearchResult[]>

describe('fake search confidence shapes → UI tiers', () => {
  it.each([
    ['perfect', 1],
    ['recommended', 2],
    ['unsure', 3],
  ])('%s lands in tier %i', (shape, tier) => {
    expect(getSearchConfidence(shapes[shape], fixture.title, fixture.artist).tier).toBe(tier)
    // The job form passes only the title in some flows; the tier must hold there too.
    expect(getSearchConfidence(shapes[shape], fixture.title).tier).toBe(tier)
  })

  it('unsure stays tier 3 with a single result', () => {
    expect(getSearchConfidence(shapes.unsure.slice(0, 1), fixture.title, fixture.artist).tier).toBe(3)
  })
})
