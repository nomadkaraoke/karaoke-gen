/**
 * @jest-environment jsdom
 *
 * BulkBatchProgress when the remembered batch can't be loaded.
 * next-intl is globally mocked in jest.setup.js (real en strings).
 */

import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { api } from '@/lib/api'
import { ApiError } from '@/lib/api-error'
import { BulkBatchProgress } from '@/components/job/bulk/BulkBatchProgress'

jest.mock('@/lib/api', () => ({ api: { getBulkBatch: jest.fn() } }))
jest.mock('@/components/audio-search/AudioSearchDialog', () => ({ AudioSearchDialog: () => null }))

const mockApi = api as jest.Mocked<typeof api>

beforeEach(() => jest.clearAllMocks())

describe('BulkBatchProgress', () => {
  it('returns to the form when the batch no longer exists (404)', async () => {
    mockApi.getBulkBatch.mockRejectedValue(new ApiError('Batch not found', 404, {}))
    const onStartAnother = jest.fn()
    render(<BulkBatchProgress batchId="gone" onStartAnother={onStartAnother} />)
    await waitFor(() => expect(onStartAnother).toHaveBeenCalled())
    expect(screen.queryByText("Couldn't load batch progress.")).toBeNull()
  })

  it('other errors show the message with a way to start another batch', async () => {
    mockApi.getBulkBatch.mockRejectedValue(new ApiError('boom', 500, {}))
    const onStartAnother = jest.fn()
    render(<BulkBatchProgress batchId="b1" onStartAnother={onStartAnother} />)
    expect(await screen.findByText("Couldn't load batch progress.")).toBeInTheDocument()
    expect(onStartAnother).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Start another batch' }))
    expect(onStartAnother).toHaveBeenCalledTimes(1)
  })
})
