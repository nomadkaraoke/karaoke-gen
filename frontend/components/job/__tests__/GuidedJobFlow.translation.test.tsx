/**
 * GuidedJobFlow — translated lyrics wiring.
 *
 * Drives the real GuidedJobFlow through all four steps (step components are
 * stubbed down to the callbacks the flow relies on) and asserts the
 * translation language chosen on the Customize step reaches every job-creation
 * call as `translation_language`, and is omitted when the option is off.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    api: {
      createJobFromSearch: jest.fn(),
      createJobFromUrl: jest.fn(),
      createJobFromUploadedAudio: jest.fn(),
    },
  }
})

jest.mock("@/lib/auth", () => ({
  useAuth: jest.fn(() => ({ user: { role: "user", credits: 5 }, isLoading: false })),
}))

jest.mock("@/lib/tenant", () => ({
  useTenant: jest.fn(() => ({ features: {} })),
}))

jest.mock("@/components/credits/BuyCreditsDialog", () => ({ BuyCreditsDialog: () => null }))
jest.mock("@/components/upload/UploadProgressModal", () => ({ UploadProgressModal: () => null }))

jest.mock("../steps/SongInfoStep", () => ({
  SongInfoStep: ({ onArtistChange, onTitleChange, onNext }: any) => (
    <button onClick={() => { onArtistChange("Artist"); onTitleChange("Song"); onNext() }}>step1-next</button>
  ),
}))

jest.mock("../steps/AudioSourceStep", () => ({
  AudioSourceStep: ({ onUrlReady, onFileReady, onSearchCompleted, onSearchResultChosen }: any) => (
    <div>
      <button onClick={() => onUrlReady("https://youtube.com/watch?v=abc")}>use-url</button>
      <button onClick={() => onFileReady(new File(["x"], "song.mp3", { type: "audio/mpeg" }))}>use-file</button>
      <button onClick={() => { onSearchCompleted("session-1"); onSearchResultChosen(0) }}>use-search</button>
    </div>
  ),
}))

jest.mock("../steps/VisibilityStep", () => ({
  VisibilityStep: ({ onNext }: any) => <button onClick={onNext}>step3-next</button>,
}))

jest.mock("../steps/CustomizeStep", () => ({
  CustomizeStep: ({ translationLanguage, onTranslationLanguageChange, onConfirm }: any) => (
    <div>
      <span data-testid="translation-value">{String(translationLanguage)}</span>
      <button onClick={() => onTranslationLanguageChange("es")}>pick-spanish</button>
      <button onClick={onConfirm}>confirm</button>
    </div>
  ),
}))

import { api } from "@/lib/api"
import { GuidedJobFlow } from "../GuidedJobFlow"

const mockApi = api as jest.Mocked<typeof api>

function goToCustomize(source: "use-url" | "use-file" | "use-search") {
  fireEvent.click(screen.getByText("step1-next"))
  fireEvent.click(screen.getByText(source))
  fireEvent.click(screen.getByText("step3-next"))
}

describe("GuidedJobFlow — translated lyrics", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockApi.createJobFromUrl.mockResolvedValue({ status: "success", job_id: "job-url", message: "ok" })
    mockApi.createJobFromSearch.mockResolvedValue({ status: "success", job_id: "job-search", message: "ok" })
    mockApi.createJobFromUploadedAudio.mockResolvedValue({ status: "success", job_id: "job-upload", message: "ok" } as any)
  })

  it("defaults to off and omits translation_language", async () => {
    render(<GuidedJobFlow onJobCreated={jest.fn()} />)
    goToCustomize("use-url")
    expect(screen.getByTestId("translation-value")).toHaveTextContent("null")

    fireEvent.click(screen.getByText("confirm"))
    await waitFor(() => expect(mockApi.createJobFromUrl).toHaveBeenCalled())
    const options = mockApi.createJobFromUrl.mock.calls[0][3]
    expect(options?.translation_language).toBeUndefined()
  })

  it("passes translation_language to createJobFromUrl", async () => {
    render(<GuidedJobFlow onJobCreated={jest.fn()} />)
    goToCustomize("use-url")
    fireEvent.click(screen.getByText("pick-spanish"))
    expect(screen.getByTestId("translation-value")).toHaveTextContent("es")

    fireEvent.click(screen.getByText("confirm"))
    await waitFor(() => expect(mockApi.createJobFromUrl).toHaveBeenCalled())
    expect(mockApi.createJobFromUrl.mock.calls[0][3]).toEqual(
      expect.objectContaining({ translation_language: "es" })
    )
  })

  it("passes translation_language to createJobFromSearch", async () => {
    render(<GuidedJobFlow onJobCreated={jest.fn()} />)
    goToCustomize("use-search")
    fireEvent.click(screen.getByText("pick-spanish"))

    fireEvent.click(screen.getByText("confirm"))
    await waitFor(() => expect(mockApi.createJobFromSearch).toHaveBeenCalled())
    expect(mockApi.createJobFromSearch).toHaveBeenCalledWith(
      expect.objectContaining({ search_session_id: "session-1", translation_language: "es" })
    )
  })

  it("passes translation_language to createJobFromUploadedAudio", async () => {
    render(<GuidedJobFlow onJobCreated={jest.fn()} />)
    goToCustomize("use-file")
    fireEvent.click(screen.getByText("pick-spanish"))

    fireEvent.click(screen.getByText("confirm"))
    await waitFor(() => expect(mockApi.createJobFromUploadedAudio).toHaveBeenCalled())
    expect(mockApi.createJobFromUploadedAudio.mock.calls[0][3]).toEqual(
      expect.objectContaining({ translation_language: "es" })
    )
  })
})
