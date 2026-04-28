import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NextIntlClientProvider } from 'next-intl'
import CustomLyricsMode from '../modals/CustomLyricsMode'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

// The global `jest.setup.js` mocks `next-intl` to read translations from
// `messages/en.json`, but the keys used by `CustomLyricsMode` are not yet
// added to en.json (Task 16). Override the mock for this test file so
// `useTranslations` resolves keys from the messages prop on
// `NextIntlClientProvider` instead.
jest.mock('next-intl', () => {
  const React = require('react') as typeof import('react')
  const MessagesContext = React.createContext({} as Record<string, unknown>)

  function getNestedValue(obj: unknown, dottedKey: string): unknown {
    return dottedKey
      .split('.')
      .reduce<unknown>(
        (o, k) => (o && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined),
        obj,
      )
  }

  return {
    NextIntlClientProvider: ({
      children,
      messages,
    }: {
      children: React.ReactNode
      messages: Record<string, unknown>
    }) =>
      React.createElement(MessagesContext.Provider, { value: messages }, children),
    useTranslations: (namespace?: string) => {
      const messages = React.useContext(MessagesContext)
      const scoped = namespace ? getNestedValue(messages, namespace) : messages
      const t = (key: string, params?: Record<string, unknown>) => {
        const value = getNestedValue(scoped, key)
        if (typeof value !== 'string') return key
        if (!params) return value
        return value.replace(/\{(\w+)\}/g, (match: string, name: string) =>
          params[name] !== undefined ? String(params[name]) : match,
        )
      }
      t.rich = (key: string, params?: Record<string, unknown>) => t(key, params)
      t.raw = (key: string) => getNestedValue(scoped, key) ?? key
      return t
    },
    useLocale: () => 'en',
    useMessages: () => ({}),
  }
})

// Mock the API client
jest.mock('@/lib/api/customLyrics', () => ({
  generateCustomLyrics: jest.fn(),
  CustomLyricsApiError: class CustomLyricsApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

const { generateCustomLyrics } = jest.requireMock('@/lib/api/customLyrics')

const messages = {
  lyricsReview: {
    modals: {
      customLyricsMode: {
        title: 'Custom Lyrics',
        description: '{count} lines expected',
        tabText: 'Paste text',
        tabFile: 'Upload file',
        textLabel: 'Custom lyrics or instructions',
        textPlaceholder: 'Paste here...',
        fileLabel: 'Upload file',
        chooseFile: 'Choose file',
        fileHint: 'Allowed: {allowed}. Max {maxMb} MB.',
        fileTypeError: 'Unsupported file. Use {allowed}.',
        fileSizeError: 'File too large (max {maxMb} MB).',
        notesLabel: 'Notes',
        notesPlaceholder: 'Wedding for John & Jane...',
        generate: 'Generate',
        cancel: 'Cancel',
        cancelGeneration: 'Cancel generation',
        generatingTitle: 'Generating...',
        generatingSubtitle: 'Up to a minute',
        generating: 'Generating',
        backToInput: 'Edit inputs',
        regenerate: 'Regenerate',
        saveCustom: 'Save',
        modelLabel: 'Model',
        previewPlaceholder: 'Generated lyrics...',
        lineCountMismatchFallback: 'Line count mismatch',
        tooManyLines: '{diff} too many lines',
        tooFewLines: '{diff} too few lines',
        genericError: 'Something went wrong',
      },
    },
  },
}

const renderMode = (props: Partial<React.ComponentProps<typeof CustomLyricsMode>> = {}) => {
  const seg = (text: string): LyricsSegment => ({
    id: `s-${text}`,
    text,
    start_time: 0,
    end_time: 1,
    words: [{ id: 'w', text, start_time: 0, end_time: 1, confidence: 1 }],
  })

  const defaults: React.ComponentProps<typeof CustomLyricsMode> = {
    open: true,
    jobId: 'job-1',
    artist: 'A',
    title: 'T',
    authToken: 'token',
    existingSegments: [seg('one'), seg('two'), seg('three')],
    onSave: jest.fn(),
    onCancel: jest.fn(),
    onBack: jest.fn(),
  }

  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <CustomLyricsMode {...defaults} {...props} />
    </NextIntlClientProvider>,
  )
}

describe('CustomLyricsMode', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders input phase by default with Generate disabled', () => {
    renderMode()
    expect(screen.getByText('Custom Lyrics')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Generate$/i })).toBeDisabled()
  })

  it('enables Generate once text is provided', async () => {
    renderMode()
    const ta = screen.getByLabelText('Custom lyrics or instructions')
    await userEvent.type(ta, 'use cats everywhere')
    expect(screen.getByRole('button', { name: /^Generate$/i })).not.toBeDisabled()
  })

  it('rejects oversize files', async () => {
    renderMode()
    await userEvent.click(screen.getByRole('tab', { name: /Upload file/i }))
    const input = screen.getByLabelText(/Choose file/i, { selector: 'input' }) as HTMLInputElement
    const big = new File([new Uint8Array(6 * 1024 * 1024)], 'big.txt', { type: 'text/plain' })
    await userEvent.upload(input, big)
    expect(await screen.findByText(/File too large/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Generate$/i })).toBeDisabled()
  })

  it('calls API and lands in preview on success with matching line count', async () => {
    generateCustomLyrics.mockResolvedValueOnce({
      lines: ['ONE', 'TWO', 'THREE'],
      warnings: [],
      model: 'gemini-3.1-pro-preview',
      lineCountMismatch: false,
      retryCount: 0,
      durationMs: 50,
    })

    const onSave = jest.fn()
    renderMode({ onSave })

    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'caps lock')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    expect(await screen.findByText(/3\/3 lines/)).toBeInTheDocument()
    const saveBtn = screen.getByRole('button', { name: /^Save$/i })
    expect(saveBtn).not.toBeDisabled()

    await userEvent.click(saveBtn)
    expect(onSave).toHaveBeenCalledTimes(1)
    const [savedSegments, meta] = onSave.mock.calls[0]
    expect(savedSegments).toHaveLength(3)
    expect(savedSegments[0].text).toBe('ONE')
    expect(meta).toMatchObject({ source: 'text', model: 'gemini-3.1-pro-preview' })
  })

  it('shows line-count mismatch warning and disables Save', async () => {
    generateCustomLyrics.mockResolvedValueOnce({
      lines: ['ONE', 'TWO'],
      warnings: ['AI returned 2 lines but 3 were expected.'],
      model: 'm',
      lineCountMismatch: true,
      retryCount: 1,
      durationMs: 5,
    })
    renderMode()
    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    expect(await screen.findByText(/AI returned 2 lines/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Save$/i })).toBeDisabled()
  })

  it('Regenerate fires another API call', async () => {
    generateCustomLyrics
      .mockResolvedValueOnce({
        lines: ['A', 'B', 'C'],
        warnings: [],
        model: 'm',
        lineCountMismatch: false,
        retryCount: 0,
        durationMs: 5,
      })
      .mockResolvedValueOnce({
        lines: ['X', 'Y', 'Z'],
        warnings: [],
        model: 'm',
        lineCountMismatch: false,
        retryCount: 0,
        durationMs: 5,
      })

    renderMode()
    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    await screen.findByText(/3\/3 lines/)
    await userEvent.click(screen.getByRole('button', { name: /Regenerate/i }))

    await waitFor(() => expect(generateCustomLyrics).toHaveBeenCalledTimes(2))
  })

  it('falls back to input phase on API error', async () => {
    generateCustomLyrics.mockRejectedValueOnce(new Error('boom'))
    renderMode()
    await userEvent.type(screen.getByLabelText('Custom lyrics or instructions'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /^Generate$/i }))

    expect(await screen.findByText(/boom/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Generate$/i })).toBeInTheDocument()
  })
})
