import { createEditLog, addEditEntry, attachFeedback } from '../editLog'
import type { EditLog } from '../../types'

describe('createEditLog', () => {
  it('creates a log with correct job_id and audio_hash', () => {
    const log = createEditLog('job-123', 'abc-hash')
    expect(log.job_id).toBe('job-123')
    expect(log.audio_hash).toBe('abc-hash')
  })

  it('generates a non-empty session_id', () => {
    const log = createEditLog('job-1', 'hash-1')
    expect(log.session_id).toBeTruthy()
    expect(typeof log.session_id).toBe('string')
  })

  it('generates unique session_ids', () => {
    const log1 = createEditLog('j', 'h')
    const log2 = createEditLog('j', 'h')
    expect(log1.session_id).not.toBe(log2.session_id)
  })

  it('sets started_at to a valid ISO timestamp', () => {
    const log = createEditLog('j', 'h')
    expect(new Date(log.started_at).toISOString()).toBe(log.started_at)
  })

  it('starts with an empty entries array', () => {
    const log = createEditLog('j', 'h')
    expect(log.entries).toEqual([])
  })
})

describe('addEditEntry', () => {
  let log: EditLog

  beforeEach(() => {
    log = createEditLog('job-1', 'hash-1')
  })

  it('appends an entry to the log', () => {
    addEditEntry(log, 'word_change', {
      text_before: 'hello',
      text_after: 'hallo',
    })
    expect(log.entries).toHaveLength(1)
  })

  it('returns the created entry', () => {
    const entry = addEditEntry(log, 'word_delete', {
      text_before: 'ghost',
    })
    expect(entry.operation).toBe('word_delete')
    expect(entry.text_before).toBe('ghost')
  })

  it('generates unique entry IDs', () => {
    const e1 = addEditEntry(log, 'word_change', {})
    const e2 = addEditEntry(log, 'word_change', {})
    expect(e1.id).not.toBe(e2.id)
  })

  it('sets timestamp on each entry', () => {
    const entry = addEditEntry(log, 'word_add', {})
    expect(new Date(entry.timestamp).toISOString()).toBe(entry.timestamp)
  })

  it('defaults optional fields correctly', () => {
    const entry = addEditEntry(log, 'segment_delete', {})
    expect(entry.segment_id).toBeNull()
    expect(entry.segment_index).toBeNull()
    expect(entry.word_ids_before).toEqual([])
    expect(entry.word_ids_after).toEqual([])
    expect(entry.text_before).toBe('')
    expect(entry.text_after).toBe('')
    expect(entry.feedback).toBeNull()
  })

  it('passes through all provided fields', () => {
    const entry = addEditEntry(log, 'word_change', {
      segment_id: 'seg-1',
      segment_index: 3,
      word_ids_before: ['w1'],
      word_ids_after: ['w2'],
      text_before: 'old',
      text_after: 'new',
      details: { custom: true },
    })
    expect(entry.segment_id).toBe('seg-1')
    expect(entry.segment_index).toBe(3)
    expect(entry.word_ids_before).toEqual(['w1'])
    expect(entry.word_ids_after).toEqual(['w2'])
    expect(entry.text_before).toBe('old')
    expect(entry.text_after).toBe('new')
    expect(entry.details).toEqual({ custom: true })
  })

  it('supports all operation types', () => {
    const ops = [
      'word_change', 'word_delete', 'word_add',
      'word_split', 'word_merge',
      'segment_delete', 'segment_add', 'segment_split', 'segment_merge',
      'find_replace', 'replace_all_lyrics', 'change_case',
      'timing_change', 'revert_correction', 'revert_all',
    ] as const

    for (const op of ops) {
      const entry = addEditEntry(log, op, {})
      expect(entry.operation).toBe(op)
    }
    expect(log.entries).toHaveLength(ops.length)
  })
})

describe('attachFeedback', () => {
  let log: EditLog

  beforeEach(() => {
    log = createEditLog('job-1', 'hash-1')
  })

  it('attaches feedback to the correct entry', () => {
    const entry = addEditEntry(log, 'word_change', { text_before: 'a' })
    attachFeedback(log, entry.id, 'misheard_word')
    expect(log.entries[0].feedback).not.toBeNull()
    expect(log.entries[0].feedback!.reason).toBe('misheard_word')
  })

  it('sets timestamp on feedback', () => {
    const entry = addEditEntry(log, 'word_delete', {})
    attachFeedback(log, entry.id, 'phantom_word')
    expect(log.entries[0].feedback!.timestamp).toBeTruthy()
    expect(new Date(log.entries[0].feedback!.timestamp).toISOString()).toBe(
      log.entries[0].feedback!.timestamp
    )
  })

  it('does nothing when entry ID is not found', () => {
    addEditEntry(log, 'word_change', {})
    attachFeedback(log, 'nonexistent-id', 'other')
    expect(log.entries[0].feedback).toBeNull()
  })

  it('overwrites previous feedback', () => {
    const entry = addEditEntry(log, 'word_change', {})
    attachFeedback(log, entry.id, 'misheard_word')
    attachFeedback(log, entry.id, 'wrong_lyrics')
    expect(log.entries[0].feedback!.reason).toBe('wrong_lyrics')
  })

  it('only modifies the targeted entry', () => {
    const e1 = addEditEntry(log, 'word_change', {})
    const e2 = addEditEntry(log, 'word_delete', {})
    attachFeedback(log, e1.id, 'misheard_word')
    expect(log.entries[0].feedback!.reason).toBe('misheard_word')
    expect(log.entries[1].feedback).toBeNull()
  })

  it('handles no_response reason', () => {
    const entry = addEditEntry(log, 'word_add', {})
    attachFeedback(log, entry.id, 'no_response')
    expect(log.entries[0].feedback!.reason).toBe('no_response')
  })
})
