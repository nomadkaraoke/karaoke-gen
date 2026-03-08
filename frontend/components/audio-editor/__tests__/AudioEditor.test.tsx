/**
 * Tests for AudioEditor component.
 *
 * Tests loading state, error handling, waveform rendering,
 * edit operations, and submit flow.
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react"
import { AudioEditor } from "../AudioEditor"
import { api, AudioEditInfo, AudioEditResponse } from "@/lib/api"

// Mock next/link
jest.mock("next/link", () => {
  return function Link({ children, href, ...props }: { children: React.ReactNode; href: string; [key: string]: unknown }) {
    return <a href={href} {...props}>{children}</a>
  }
})

// Mock next/image
jest.mock("next/image", () => ({
  __esModule: true,
  default: (props: Record<string, unknown>) => <img {...props} />,
}))

// Mock ThemeToggle
jest.mock("@/components/ThemeToggle", () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}))

// Mock auto-save hook
jest.mock("@/hooks/use-audio-edit-autosave", () => ({
  useAudioEditAutoSave: () => ({ saveSession: jest.fn() }),
}))

// Mock canvas
HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
  fillRect: jest.fn(),
  fillText: jest.fn(),
  measureText: jest.fn(() => ({ width: 20 })),
  beginPath: jest.fn(),
  moveTo: jest.fn(),
  lineTo: jest.fn(),
  stroke: jest.fn(),
  setLineDash: jest.fn(),
  fillStyle: "",
  strokeStyle: "",
  lineWidth: 1,
  font: "",
  textBaseline: "",
})) as jest.Mock

// Mock api
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api")
  return {
    ...actual,
    api: {
      getInputAudioInfo: jest.fn(),
      applyAudioEdit: jest.fn(),
      undoAudioEdit: jest.fn(),
      redoAudioEdit: jest.fn(),
      uploadAudioForJoin: jest.fn(),
      submitAudioEdit: jest.fn(),
      listAudioEditSessions: jest.fn(),
      saveAudioEditSession: jest.fn(),
      getAudioEditSession: jest.fn(),
    },
  }
})

const mockApi = api as jest.Mocked<typeof api>

const mockJob = {
  job_id: "test-job-123",
  status: "in_audio_edit",
  progress: 50,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  artist: "Test Artist",
  title: "Test Song",
}

const mockAudioInfo: AudioEditInfo = {
  job_id: "test-job-123",
  artist: "Test Artist",
  title: "Test Song",
  original_duration_seconds: 200,
  current_duration_seconds: 200,
  original_audio_url: "https://example.com/original.ogg",
  current_audio_url: "https://example.com/current.ogg",
  waveform_data: { amplitudes: Array(100).fill(0.5) },
  original_waveform_data: { amplitudes: Array(100).fill(0.5) },
  edit_stack: [],
  can_undo: false,
  can_redo: false,
}

const mockEditResponse: AudioEditResponse = {
  status: "success",
  edit_id: "edit-1",
  operation: "trim_start",
  duration_before: 200,
  duration_after: 180,
  current_audio_url: "https://example.com/edited.ogg",
  waveform_data: { amplitudes: Array(90).fill(0.5) },
  edit_stack: [
    {
      edit_id: "edit-1",
      operation: "trim_start",
      params: { end_seconds: 20 },
      duration_before: 200,
      duration_after: 180,
      timestamp: new Date().toISOString(),
    },
  ],
  can_undo: true,
  can_redo: false,
}

describe("AudioEditor", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    // Mock localStorage
    Storage.prototype.getItem = jest.fn(() => null)
    Storage.prototype.setItem = jest.fn()
    // Default: no saved sessions
    mockApi.listAudioEditSessions.mockResolvedValue({ sessions: [] })
    mockApi.saveAudioEditSession.mockResolvedValue({ status: "saved", session_id: "s1" })
  })

  it("shows loading state initially", () => {
    mockApi.getInputAudioInfo.mockReturnValue(new Promise(() => {})) // Never resolves
    render(<AudioEditor job={mockJob} />)
    expect(screen.getByText("Loading audio editor...")).toBeInTheDocument()
  })

  it("shows error state when loading fails", async () => {
    mockApi.getInputAudioInfo.mockRejectedValue(new Error("Failed to load"))
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Failed to load audio")).toBeInTheDocument()
    })
  })

  it("renders editor after loading", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Audio Editor")).toBeInTheDocument()
    })
    expect(screen.getByText("Test Artist - Test Song")).toBeInTheDocument()
  })

  it("shows guidance panel by default", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Audio Editor Tips")).toBeInTheDocument()
    })
  })

  it("shows Original and Edited tabs", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText(/Original/)).toBeInTheDocument()
      expect(screen.getByText(/Edited/)).toBeInTheDocument()
    })
  })

  it("shows 'Continue Without Editing' when no edits", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Continue Without Editing")).toBeInTheDocument()
    })
  })

  it("shows 'Submit & Continue' when there are edits", async () => {
    const infoWithEdits = {
      ...mockAudioInfo,
      current_duration_seconds: 180,
      edit_stack: mockEditResponse.edit_stack,
      can_undo: true,
    }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithEdits)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Submit & Continue")).toBeInTheDocument()
    })
  })

  it("shows edit history when there are edits", async () => {
    const infoWithEdits = {
      ...mockAudioInfo,
      current_duration_seconds: 180,
      edit_stack: mockEditResponse.edit_stack,
      can_undo: true,
    }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithEdits)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText(/Edit History/)).toBeInTheDocument()
    })
  })

  it("submits without confirm when no edits", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    mockApi.submitAudioEdit.mockResolvedValue({ status: "success", message: "ok", job_id: "test-job-123" })

    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Continue Without Editing")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Continue Without Editing"))
    await waitFor(() => {
      expect(mockApi.submitAudioEdit).toHaveBeenCalledWith("test-job-123", undefined)
    })
  })

  it("shows confirm dialog when submitting with edits", async () => {
    const infoWithEdits = {
      ...mockAudioInfo,
      current_duration_seconds: 180,
      edit_stack: mockEditResponse.edit_stack,
      can_undo: true,
    }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithEdits)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Submit & Continue")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Submit & Continue"))
    await waitFor(() => {
      expect(screen.getByText("Submit Edited Audio?")).toBeInTheDocument()
    })
  })

  it("shows success screen after submit", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    mockApi.submitAudioEdit.mockResolvedValue({ status: "success", message: "ok", job_id: "test-job-123" })

    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Continue Without Editing")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Continue Without Editing"))
    await waitFor(() => {
      expect(screen.getByText("Audio edit complete!")).toBeInTheDocument()
    })
  })

  it("shows undo/redo buttons", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByTitle("Undo (Ctrl+Z)")).toBeInTheDocument()
      expect(screen.getByTitle("Redo (Ctrl+Shift+Z)")).toBeInTheDocument()
    })
  })

  it("disables undo when canUndo is false", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByTitle("Undo (Ctrl+Z)")).toBeDisabled()
    })
  })

  it("enables undo when canUndo is true", async () => {
    const infoWithUndo = { ...mockAudioInfo, can_undo: true, edit_stack: mockEditResponse.edit_stack }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithUndo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByTitle("Undo (Ctrl+Z)")).not.toBeDisabled()
    })
  })

  it("calls undo API when clicking undo", async () => {
    const infoWithUndo = {
      ...mockAudioInfo,
      can_undo: true,
      edit_stack: mockEditResponse.edit_stack,
      current_duration_seconds: 180,
    }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithUndo)
    mockApi.undoAudioEdit.mockResolvedValue({
      ...mockEditResponse,
      edit_stack: [],
      can_undo: false,
      can_redo: true,
      duration_after: 200,
    })

    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByTitle("Undo (Ctrl+Z)")).not.toBeDisabled()
    })

    fireEvent.click(screen.getByTitle("Undo (Ctrl+Z)"))
    await waitFor(() => {
      expect(mockApi.undoAudioEdit).toHaveBeenCalledWith("test-job-123")
    })
  })

  it("shows Join button", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Join")).toBeInTheDocument()
    })
  })

  it("opens join dialog when clicking Join", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Join")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Join"))
    await waitFor(() => {
      expect(screen.getByText("Join Audio")).toBeInTheDocument()
    })
  })

  it("shows playback controls", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByTitle("Back 5s")).toBeInTheDocument()
      expect(screen.getByTitle("Forward 5s")).toBeInTheDocument()
    })
  })

  it("shows restore dialog when saved sessions exist", async () => {
    const sessions = [{
      session_id: "s1",
      job_id: "test-job-123",
      edit_count: 2,
      trigger: "auto",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }]
    mockApi.listAudioEditSessions.mockResolvedValue({ sessions })
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    mockApi.getAudioEditSession.mockResolvedValue({
      ...sessions[0],
      edit_data: { entries: mockEditResponse.edit_stack },
    })

    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Saved Audio Edit Sessions")).toBeInTheDocument()
    })
  })

  it("does not show restore dialog when no sessions exist", async () => {
    mockApi.listAudioEditSessions.mockResolvedValue({ sessions: [] })
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)

    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Audio Editor")).toBeInTheDocument()
    })
    expect(screen.queryByText("Saved Audio Edit Sessions")).not.toBeInTheDocument()
  })

  it("does not show restore dialog when edits already applied", async () => {
    const sessions = [{
      session_id: "s1",
      job_id: "test-job-123",
      edit_count: 2,
      trigger: "auto",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }]
    mockApi.listAudioEditSessions.mockResolvedValue({ sessions })
    const infoWithEdits = {
      ...mockAudioInfo,
      edit_stack: mockEditResponse.edit_stack,
      can_undo: true,
    }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithEdits)

    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Audio Editor")).toBeInTheDocument()
    })
    expect(screen.queryByText("Saved Audio Edit Sessions")).not.toBeInTheDocument()
  })

  it("shows Save button when there are edits", async () => {
    const infoWithEdits = {
      ...mockAudioInfo,
      current_duration_seconds: 180,
      edit_stack: mockEditResponse.edit_stack,
      can_undo: true,
    }
    mockApi.getInputAudioInfo.mockResolvedValue(infoWithEdits)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Save")).toBeInTheDocument()
    })
  })

  it("does not show Save button when no edits", async () => {
    mockApi.getInputAudioInfo.mockResolvedValue(mockAudioInfo)
    render(<AudioEditor job={mockJob} />)
    await waitFor(() => {
      expect(screen.getByText("Audio Editor")).toBeInTheDocument()
    })
    expect(screen.queryByText("Save")).not.toBeInTheDocument()
  })
})
