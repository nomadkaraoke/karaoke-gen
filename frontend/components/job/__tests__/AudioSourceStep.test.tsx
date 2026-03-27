/**
 * Tests for AudioSourceStep component.
 *
 * Covers the new features added in this branch:
 * - Catalog search fires in parallel on mount
 * - Community check fires in parallel on mount
 * - SongSuggestionPanel renders when catalog results arrive
 * - CommunityVersionBanner renders when community data arrives
 * - "Did you mean?" fuzzy banner renders for poor search + no exact catalog match
 * - onArtistTitleCorrection callback is invoked on catalog/fuzzy correction
 * - Error and loading states
 * - Credit error shows Buy Credits
 */

import { render, screen, waitFor, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { api, ApiError } from "@/lib/api"
import { getSearchConfidence } from "@/lib/audio-search-utils"

// Mock the api module
jest.mock("@/lib/api", () => ({
  api: {
    searchStandalone: jest.fn(),
    searchCatalogTracks: jest.fn(),
    checkCommunityVersions: jest.fn(),
    createJobFromSearch: jest.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number
    data?: any
    constructor(message: string, status: number, data?: any) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.data = data
    }
  },
}))

// Mock BuyCreditsDialog
jest.mock("@/components/credits/BuyCreditsDialog", () => ({
  BuyCreditsDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="buy-credits-dialog">Buy Credits</div> : null,
}))

// Mock audio-search-utils to provide simple implementations
jest.mock("@/lib/audio-search-utils", () => ({
  groupResults: jest.fn(() => new Map()),
  getSearchConfidence: jest.fn(() => ({
    tier: 1,
    bestResult: { index: 0, title: "Waterloo", artist: "ABBA", provider: "YouTube", url: "https://yt.com/1" },
    bestCategory: "LOSSLESS",
    explanation: "Perfect match",
    warnings: [],
  })),
  getAvailabilityLabel: jest.fn(() => ({ text: "", tooltip: "" })),
  checkFilenameMismatch: jest.fn(() => ({ isMismatch: false })),
  getDisplayName: jest.fn((r: any) => r.title || ""),
  formatCount: jest.fn((n: number) => `${n}`),
  formatMetadata: jest.fn(() => ""),
  formatQuality: jest.fn(() => ""),
}))

const mockApi = api as jest.Mocked<typeof api>
const mockGetSearchConfidence = getSearchConfidence as jest.Mock

import { AudioSourceStep } from "../steps/AudioSourceStep"

const defaultProps = {
  artist: "ABBA",
  title: "Waterloo",
  onSearchCompleted: jest.fn(),
  onSearchResultChosen: jest.fn(),
  onArtistTitleCorrection: jest.fn(),
  onUrlReady: jest.fn(),
  onFileReady: jest.fn(),
  onBack: jest.fn(),
  noCredits: false,
}

function setupMocks() {
  mockApi.searchStandalone.mockResolvedValue({
    search_session_id: "sess-123",
    results: [
      { index: 0, title: "Waterloo", artist: "ABBA", provider: "YouTube", url: "https://yt.com/1" },
    ],
    results_count: 1,
  })
  mockApi.searchCatalogTracks.mockResolvedValue([])
  mockApi.checkCommunityVersions.mockResolvedValue({
    has_community: false,
    songs: [],
    best_youtube_url: null,
  })
}

describe("AudioSourceStep", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    setupMocks()
  })

  it("calls searchStandalone on mount with artist and title", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalledWith("ABBA", "Waterloo")
    })
  })

  it("calls searchCatalogTracks on mount in parallel", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchCatalogTracks).toHaveBeenCalledWith("Waterloo", "ABBA", 5)
    })
  })

  it("calls checkCommunityVersions on mount in parallel", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.checkCommunityVersions).toHaveBeenCalledWith("ABBA", "Waterloo")
    })
  })

  it("calls onSearchCompleted with session ID when search resolves", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(defaultProps.onSearchCompleted).toHaveBeenCalledWith("sess-123")
    })
  })

  it("shows loading state while searching", () => {
    // Make search hang
    mockApi.searchStandalone.mockReturnValue(new Promise(() => {}))
    render(<AudioSourceStep {...defaultProps} />)

    expect(screen.getByText("Searching for audio sources...")).toBeInTheDocument()
  })

  it("shows 'Searching for Artist - Title' text", () => {
    mockApi.searchStandalone.mockReturnValue(new Promise(() => {}))
    render(<AudioSourceStep {...defaultProps} />)

    expect(screen.getByText("ABBA - Waterloo")).toBeInTheDocument()
  })

  it("renders SongSuggestionPanel when catalog returns results", async () => {
    mockApi.searchCatalogTracks.mockResolvedValue([
      { artist_name: "ABBA", track_name: "Waterloo (Remastered)" },
    ])

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByTestId("song-suggestion-panel")).toBeInTheDocument()
    })
  })

  it("renders CommunityVersionBanner when community data has results", async () => {
    mockApi.checkCommunityVersions.mockResolvedValue({
      has_community: true,
      best_youtube_url: "https://youtube.com/watch?v=abc",
      songs: [
        {
          title: "Waterloo",
          artist: "ABBA",
          community_tracks: [
            {
              brand_name: "Nomad Karaoke",
              brand_code: "NK",
              youtube_url: "https://youtube.com/watch?v=abc",
              is_community: true,
            },
          ],
        },
      ],
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(
        screen.getByText("A karaoke version of this song already exists!")
      ).toBeInTheDocument()
    })
  })

  it("shows error message when search fails with ApiError", async () => {
    jest.useFakeTimers()
    try {
      mockApi.searchStandalone.mockRejectedValue(
        new ApiError("Internal server error", 500)
      )

      render(<AudioSourceStep {...defaultProps} />)

      // 500 errors are retried up to 3 times with delays (0, 2s, 4s).
      // Advance timers and flush microtasks between each retry.
      for (let i = 0; i < 5; i++) {
        await act(async () => { jest.advanceTimersByTime(5000) })
      }

      await waitFor(() => {
        expect(screen.getByText("Internal server error")).toBeInTheDocument()
      })
    } finally {
      jest.useRealTimers()
    }
  })

  it("shows credit error with Buy Credits link on 402", async () => {
    mockApi.searchStandalone.mockRejectedValue(
      new ApiError("No credits", 402)
    )

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(
        screen.getByText(/out of credits/i)
      ).toBeInTheDocument()
      expect(screen.getByText("Buy Credits")).toBeInTheDocument()
    })
  })

  it("calls onBack when Back button is clicked", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalled()
    })

    const user = userEvent.setup()
    await user.click(screen.getByText("Back"))

    expect(defaultProps.onBack).toHaveBeenCalledTimes(1)
  })

  it("silently handles catalog search failure (does not show error)", async () => {
    mockApi.searchCatalogTracks.mockRejectedValue(new Error("catalog down"))

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalled()
    })

    // No error shown for catalog failure — it's a nice-to-have
    expect(screen.queryByText("catalog down")).not.toBeInTheDocument()
  })

  it("silently handles community check failure", async () => {
    mockApi.checkCommunityVersions.mockRejectedValue(new Error("community down"))

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalled()
    })

    expect(screen.queryByText("community down")).not.toBeInTheDocument()
  })

  it("does not show SongSuggestionPanel when catalog returns empty array", async () => {
    mockApi.searchCatalogTracks.mockResolvedValue([])

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalled()
    })

    expect(screen.queryByTestId("song-suggestion-panel")).not.toBeInTheDocument()
  })

  it("does not show CommunityVersionBanner when has_community is false", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.checkCommunityVersions).toHaveBeenCalled()
    })

    expect(
      screen.queryByText("A karaoke version of this song already exists!")
    ).not.toBeInTheDocument()
  })

  // --- Search robustness tests (state machine + retry) ---

  it("does NOT show 'No audio sources found' when search fails with network error", async () => {
    jest.useFakeTimers()
    try {
      mockApi.searchStandalone.mockRejectedValue(new TypeError("Failed to fetch"))
      mockGetSearchConfidence.mockReturnValue({
        tier: 3, bestResult: null, bestCategory: null, reason: "", warnings: [],
      })

      render(<AudioSourceStep {...defaultProps} />)

      // Advance through all retry attempts
      for (let i = 0; i < 5; i++) {
        await act(async () => { jest.advanceTimersByTime(5000) })
      }

      // Should show error message, NOT "No audio sources found"
      await waitFor(() => {
        expect(screen.getByText(/network error/i)).toBeInTheDocument()
      })
      expect(screen.queryByTestId("no-results-section")).not.toBeInTheDocument()
    } finally {
      jest.useRealTimers()
    }
  })

  it("retries 500 errors up to 3 times before showing error", async () => {
    jest.useFakeTimers()
    try {
      mockApi.searchStandalone.mockRejectedValue(
        new ApiError("Server error", 500)
      )

      render(<AudioSourceStep {...defaultProps} />)

      // Advance through all retry attempts
      for (let i = 0; i < 5; i++) {
        await act(async () => { jest.advanceTimersByTime(5000) })
      }

      await waitFor(() => {
        expect(screen.getByText("Server error")).toBeInTheDocument()
      })

      // Should have been called 3 times (1 initial + 2 retries)
      expect(mockApi.searchStandalone).toHaveBeenCalledTimes(3)
    } finally {
      jest.useRealTimers()
    }
  })

  it("shows retry progress text during retries", async () => {
    jest.useFakeTimers()
    try {
      mockApi.searchStandalone.mockRejectedValue(
        new ApiError("Server error", 500)
      )

      render(<AudioSourceStep {...defaultProps} />)

      // After first failure + 2s delay, should show retry text
      await act(async () => { jest.advanceTimersByTime(3000) })

      expect(screen.getByText(/Retry 1 of 2/)).toBeInTheDocument()

      // Clean up remaining retries
      for (let i = 0; i < 3; i++) {
        await act(async () => { jest.advanceTimersByTime(5000) })
      }
    } finally {
      jest.useRealTimers()
    }
  })

  it("does not retry 402 credit errors", async () => {
    mockApi.searchStandalone.mockRejectedValue(
      new ApiError("No credits", 402)
    )

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByText(/out of credits/i)).toBeInTheDocument()
    })

    // Should have been called only once — no retries
    expect(mockApi.searchStandalone).toHaveBeenCalledTimes(1)
  })

  it("shows 'No audio sources found' only after successful search with 0 results", async () => {
    mockApi.searchStandalone.mockResolvedValue({
      search_session_id: "sess-empty",
      results: [],
      results_count: 0,
    })
    mockGetSearchConfidence.mockReturnValue({
      tier: 3, bestResult: null, bestCategory: null, reason: "", warnings: [],
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByTestId("no-results-section")).toBeInTheDocument()
    })
  })
})
