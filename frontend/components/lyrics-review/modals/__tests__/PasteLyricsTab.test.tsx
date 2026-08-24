import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PasteLyricsTab from '../PasteLyricsTab'
import type { AddLyricsResult } from '@/lib/lyrics-review/types'

describe('PasteLyricsTab', () => {
  const successResult: AddLyricsResult = {
    status: 'success',
    data: {} as AddLyricsResult['data'],
  }

  const rejectedResult: AddLyricsResult = {
    status: 'rejected',
    rejection: { relevance: 0.013, matched_words: 3, total_words: 230 },
    data: {} as AddLyricsResult['data'],
  }

  async function fillAndSubmit(onAdd: jest.Mock, onClose: jest.Mock) {
    const user = userEvent.setup()
    render(<PasteLyricsTab onAdd={onAdd} onClose={onClose} />)

    await user.type(screen.getByLabelText('Source Name'), 'manual')
    await user.type(screen.getByLabelText('Lyrics'), 'Some lyrics text')
    await user.click(screen.getByRole('button', { name: 'Add Lyrics' }))
    return user
  }

  it('clears fields and closes on success', async () => {
    const onAdd = jest.fn().mockResolvedValue(successResult)
    const onClose = jest.fn()

    await fillAndSubmit(onAdd, onClose)

    expect(onAdd).toHaveBeenCalledWith('manual', 'Some lyrics text', false)
    expect(onClose).toHaveBeenCalled()
    expect(screen.getByLabelText('Lyrics')).toHaveValue('')
  })

  it('keeps fields, shows warning, and offers Add Anyway when rejected', async () => {
    const onAdd = jest.fn().mockResolvedValue(rejectedResult)
    const onClose = jest.fn()

    await fillAndSubmit(onAdd, onClose)

    // Lyrics preserved, modal stays open
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Lyrics')).toHaveValue('Some lyrics text')

    // Warning mentions the match percentage (1.3% → 1%)
    expect(screen.getByText(/only matched 1%/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add Anyway' })).toBeInTheDocument()
  })

  it('retries with force=true via Add Anyway and closes on success', async () => {
    const onAdd = jest
      .fn()
      .mockResolvedValueOnce(rejectedResult)
      .mockResolvedValueOnce(successResult)
    const onClose = jest.fn()

    const user = await fillAndSubmit(onAdd, onClose)

    await user.click(screen.getByRole('button', { name: 'Add Anyway' }))

    expect(onAdd).toHaveBeenLastCalledWith('manual', 'Some lyrics text', true)
    expect(onClose).toHaveBeenCalled()
  })

  it('clears the rejection warning when the lyrics are edited', async () => {
    const onAdd = jest.fn().mockResolvedValue(rejectedResult)
    const onClose = jest.fn()

    const user = await fillAndSubmit(onAdd, onClose)
    expect(screen.getByText(/only matched/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Lyrics'), ' more words')

    expect(screen.queryByText(/only matched/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add Anyway' })).not.toBeInTheDocument()
  })

  it('handles legacy void result as success', async () => {
    // Local-mode/legacy onAdd may resolve with no result — treat as success
    const onAdd = jest.fn().mockResolvedValue(undefined)
    const onClose = jest.fn()

    await fillAndSubmit(onAdd, onClose)

    expect(onClose).toHaveBeenCalled()
  })
})
