import { createLocalSessionStore } from '../localSessionStore'
import type { CorrectionData } from '../../types'
import type { ReviewSessionSummary } from '@/lib/api'

const summary: ReviewSessionSummary = {
  total_segments: 1,
  total_words: 2,
  corrections_made: 1,
  changed_words: [{ original: 'where', corrected: 'when', segment_index: 0 }],
}

function makeData(text: string): CorrectionData {
  return {
    corrected_segments: [
      { id: 's1', text, start_time: 0, end_time: 1, words: [{ id: 'w1', text, start_time: 0, end_time: 1 }] },
    ],
  } as unknown as CorrectionData
}

describe('localSessionStore', () => {
  beforeEach(() => localStorage.clear())

  it('lists nothing before any save', async () => {
    const store = createLocalSessionStore({ storeKey: 'job-1', jobId: 'job-1' })
    expect((await store.listReviewSessions()).sessions).toEqual([])
  })

  it('saves, lists (without data), and restores with data', async () => {
    const store = createLocalSessionStore({ storeKey: 'job-1', jobId: 'job-1', title: 'T', artist: 'A' })
    const saved = await store.saveReviewSession(makeData('when'), 3, 'auto', summary)
    expect(saved.status).toBe('saved')

    const { sessions } = await store.listReviewSessions()
    expect(sessions).toHaveLength(1)
    expect(sessions[0].edit_count).toBe(3)
    expect(sessions[0].title).toBe('T')
    // list payload must not carry the full correction_data
    expect('correction_data' in sessions[0]).toBe(false)

    const full = await store.getReviewSession(sessions[0].session_id)
    expect(full.correction_data?.corrected_segments[0].words[0].text).toBe('when')
  })

  it('upserts the rolling session rather than appending', async () => {
    const store = createLocalSessionStore({ storeKey: 'job-1', jobId: 'job-1' })
    const a = await store.saveReviewSession(makeData('one'), 1, 'auto', summary)
    const b = await store.saveReviewSession(makeData('two'), 5, 'manual', summary)
    expect(a.session_id).toBe(b.session_id) // same rolling session

    const { sessions } = await store.listReviewSessions()
    expect(sessions).toHaveLength(1)
    expect(sessions[0].edit_count).toBe(5)
  })

  it('isolates sessions per storeKey', async () => {
    const a = createLocalSessionStore({ storeKey: 'job-a', jobId: 'job-a' })
    const b = createLocalSessionStore({ storeKey: 'job-b', jobId: 'job-b' })
    await a.saveReviewSession(makeData('a'), 1, 'auto', summary)
    expect((await b.listReviewSessions()).sessions).toEqual([])
    expect((await a.listReviewSessions()).sessions).toHaveLength(1)
  })

  it('deletes a session', async () => {
    const store = createLocalSessionStore({ storeKey: 'job-1', jobId: 'job-1' })
    const { session_id } = await store.saveReviewSession(makeData('x'), 1, 'auto', summary)
    await store.deleteReviewSession(session_id!)
    expect((await store.listReviewSessions()).sessions).toEqual([])
  })
})
