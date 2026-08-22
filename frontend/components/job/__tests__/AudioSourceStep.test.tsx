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
    matchJudge: jest.fn(),
    createJobFromSearch: jest.fn(),
    validateJobUrl: jest.fn().mockResolvedValue({ supported: true, detail: null }),
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
  onAudioEditChange: jest.fn(),
  noCredits: false,
}

function verdict(overrides: Partial<import("@/lib/api").MatchJudgeVerdict> = {}) {
  return {
    kind: "none" as const,
    confident: true,
    canonical_artist: "ABBA",
    canonical_title: "Waterloo",
    alternatives: [],
    engine: "catalog" as const,
    reason: "",
    needs_ai: false,
    ...overrides,
  }
}

/** Stage-aware matchJudge mock: the component fires a "fast" pass on mount and a
 *  "full" pass (with the tier) only when the fast pass is inconclusive / weak. */
function setJudgeVerdicts(opts: {
  fast?: Partial<import("@/lib/api").MatchJudgeVerdict>
  full?: Partial<import("@/lib/api").MatchJudgeVerdict>
}) {
  mockApi.matchJudge.mockImplementation((_a, _t, callOpts) => {
    const isFast = callOpts?.stage === "fast"
    return Promise.resolve(verdict(isFast ? opts.fast : opts.full))
  })
}

function setupMocks() {
  // Reset confidence to the tier-1 default each test (mockReturnValue otherwise
  // persists across tests because clearAllMocks only clears call history).
  mockGetSearchConfidence.mockReturnValue({
    tier: 1,
    bestResult: { index: 0, title: "Waterloo", artist: "ABBA", provider: "YouTube", url: "https://yt.com/1" },
    bestCategory: "LOSSLESS",
    reason: "Perfect match",
    warnings: [],
  })
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
  // Default: catalog says "already canonical" on both passes (no change).
  setJudgeVerdicts({ fast: {}, full: {} })
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

  it("blocks an unsupported (DRM) URL at 'Use This URL' and shows guidance without advancing", async () => {
    mockApi.validateJobUrl.mockResolvedValue({
      supported: false,
      detail:
        "Apple Music links are copy-protected, so we can't download them directly. " +
        "You can download the track using a tool like https://am-dl.pages.dev, " +
        "https://aplmate.com and then upload the audio file here.",
    })

    const user = userEvent.setup()
    render(<AudioSourceStep {...defaultProps} />)

    // Open the "paste a link" fallback form (appears once search settles).
    const pasteLink = await screen.findByRole("button", { name: /Paste a link/i })
    await user.click(pasteLink)

    const input = screen.getByPlaceholderText(/youtube\.com\/watch/i)
    const appleUrl = "https://music.apple.com/us/album/x/1?i=2"
    await user.type(input, appleUrl)

    const useUrlBtn = screen.getByRole("button", { name: /Use This URL/i })
    await waitFor(() => expect(useUrlBtn).not.toBeDisabled())
    await user.click(useUrlBtn)

    await waitFor(() => {
      expect(mockApi.validateJobUrl).toHaveBeenCalledWith(appleUrl)
    })

    // Guidance is shown with a clickable downloader link, and we did NOT advance.
    expect(await screen.findByText(/copy-protected/i)).toBeInTheDocument()
    const link = screen.getByRole("link", { name: "https://am-dl.pages.dev" })
    expect(link).toHaveAttribute("href", "https://am-dl.pages.dev")
    expect(defaultProps.onUrlReady).not.toHaveBeenCalled()
  })

  it("fires a fast catalog-only pass on mount (parallel with search)", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.matchJudge).toHaveBeenCalledWith("ABBA", "Waterloo", { stage: "fast" })
    })
  })

  it("fires a full pass with the tier when the fast pass is inconclusive", async () => {
    setJudgeVerdicts({
      fast: { kind: "none", confident: false, needs_ai: true, engine: "catalog" },
      full: { kind: "none", confident: true },
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.matchJudge).toHaveBeenCalledWith(
        "ABBA", "Waterloo", { stage: "full", audioConfidenceTier: 1 }
      )
    })
  })

  it("does NOT fire a full pass when the fast pass is confident and audio is strong", async () => {
    // Default mocks: fast returns a confident catalog verdict, tier 1.
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.matchJudge).toHaveBeenCalledWith("ABBA", "Waterloo", { stage: "fast" })
    })
    // Give the coordinating effect a chance to (not) fire the full call.
    await waitFor(() => {
      expect(mockApi.matchJudge).toHaveBeenCalledTimes(1)
    })
    expect(mockApi.matchJudge).not.toHaveBeenCalledWith(
      "ABBA", "Waterloo", expect.objectContaining({ stage: "full" })
    )
  })

  it("fires a full pass to verify a catalog match when audio results are weak (tier 3)", async () => {
    mockApi.searchStandalone.mockResolvedValue({
      search_session_id: "sess-empty", results: [], results_count: 0,
    })
    mockGetSearchConfidence.mockReturnValue({
      tier: 3, bestResult: null, bestCategory: null, reason: "", warnings: [],
    })
    setJudgeVerdicts({
      fast: { kind: "none", confident: true, engine: "catalog" }, // catalog "matched"
      full: { kind: "content", confident: true, canonical_title: "Waterloo (Fixed)", engine: "ai" },
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.matchJudge).toHaveBeenCalledWith(
        "ABBA", "Waterloo", { stage: "full", audioConfidenceTier: 3 }
      )
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

  it("auto-applies a cosmetic tidy from the fast pass and shows the tidy notice", async () => {
    setJudgeVerdicts({
      fast: {
        kind: "cosmetic",
        confident: true,
        canonical_title: "Waterloo (Remastered)",
        engine: "catalog",
      },
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(defaultProps.onArtistTitleCorrection).toHaveBeenCalledWith("ABBA", "Waterloo (Remastered)")
    })
    expect(screen.getByTestId("match-tidy-notice")).toBeInTheDocument()
  })

  it("does not auto-apply an ambiguous match — shows an ask instead", async () => {
    setJudgeVerdicts({
      fast: { kind: "none", confident: false, needs_ai: true, engine: "catalog" },
      full: {
        kind: "ambiguous",
        confident: false,
        canonical_artist: "Lewis Capaldi",
        canonical_title: "Bruises",
        alternatives: [{ artist: "Fox Stevenson", title: "Bruises" }],
        engine: "ai",
      },
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(screen.getByTestId("match-didyoumean")).toBeInTheDocument()
    })
    expect(defaultProps.onArtistTitleCorrection).not.toHaveBeenCalled()

    const user = userEvent.setup()
    await user.click(screen.getByText("Lewis Capaldi — Bruises"))
    expect(defaultProps.onArtistTitleCorrection).toHaveBeenCalledWith("Lewis Capaldi", "Bruises")
  })

  it("gates the pick button until the judge settles, then enables it", async () => {
    // Fast pass is inconclusive; full pass resolves after a tick → gate releases.
    let resolveFull: (v: import("@/lib/api").MatchJudgeVerdict) => void = () => {}
    mockApi.matchJudge.mockImplementation((_a, _t, callOpts) => {
      if (callOpts?.stage === "fast") {
        return Promise.resolve(verdict({ kind: "none", confident: false, needs_ai: true }))
      }
      return new Promise<import("@/lib/api").MatchJudgeVerdict>((res) => { resolveFull = res })
    })

    render(<AudioSourceStep {...defaultProps} />)

    // Pick button rendered but disabled while the full pass is in flight.
    const pickButton = await screen.findByRole("button", { name: /Use This Audio/i })
    expect(pickButton).toBeDisabled()
    expect(screen.getByTestId("match-gate-checking")).toBeInTheDocument()

    // Resolve the full pass → gate releases, button enables.
    await act(async () => {
      resolveFull(verdict({ kind: "none", confident: true }))
    })
    await waitFor(() => expect(pickButton).toBeEnabled())
    expect(screen.queryByTestId("match-gate-checking")).not.toBeInTheDocument()
  })

  it("releases the gate via the safety timeout if the judge hangs", async () => {
    jest.useFakeTimers()
    try {
      mockApi.matchJudge.mockImplementation((_a, _t, callOpts) => {
        if (callOpts?.stage === "fast") {
          return Promise.resolve(verdict({ kind: "none", confident: false, needs_ai: true }))
        }
        return new Promise(() => {}) // full pass never resolves
      })

      render(<AudioSourceStep {...defaultProps} />)

      // Let the fast pass + coordinating effect run.
      await act(async () => { await Promise.resolve() })
      const pickButton = await screen.findByRole("button", { name: /Use This Audio/i })
      expect(pickButton).toBeDisabled()

      // Advance past the 12s safety timeout → gate releases.
      await act(async () => { jest.advanceTimersByTime(12000) })
      await waitFor(() => expect(pickButton).toBeEnabled())
    } finally {
      jest.useRealTimers()
    }
  })

  it("toggles a cosmetic tidy back and forth (two-way)", async () => {
    setJudgeVerdicts({
      fast: { kind: "cosmetic", confident: true, canonical_title: "Waterloo (Remastered)", engine: "catalog" },
    })

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(defaultProps.onArtistTitleCorrection).toHaveBeenCalledWith("ABBA", "Waterloo (Remastered)")
    })

    const user = userEvent.setup()
    // Revert to what the user typed.
    await user.click(screen.getByText("keep what I typed"))
    expect(defaultProps.onArtistTitleCorrection).toHaveBeenLastCalledWith("ABBA", "Waterloo")
    expect(screen.getByText(/Using what you typed/)).toBeInTheDocument()

    // Switch back to the tidied version.
    await user.click(screen.getByText("use tidied version"))
    expect(defaultProps.onArtistTitleCorrection).toHaveBeenLastCalledWith("ABBA", "Waterloo (Remastered)")
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

  it("silently handles matchJudge failure (does not show error)", async () => {
    mockApi.matchJudge.mockRejectedValue(new Error("judge down"))

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalled()
    })

    // No error shown for judge failure — it's a nice-to-have
    expect(screen.queryByText("judge down")).not.toBeInTheDocument()
  })

  it("silently handles community check failure", async () => {
    mockApi.checkCommunityVersions.mockRejectedValue(new Error("community down"))

    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.searchStandalone).toHaveBeenCalled()
    })

    expect(screen.queryByText("community down")).not.toBeInTheDocument()
  })

  it("shows no match notice for a 'none' verdict", async () => {
    render(<AudioSourceStep {...defaultProps} />)

    await waitFor(() => {
      expect(mockApi.matchJudge).toHaveBeenCalled()
    })

    expect(screen.queryByTestId("match-tidy-notice")).not.toBeInTheDocument()
    expect(screen.queryByTestId("match-didyoumean")).not.toBeInTheDocument()
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
