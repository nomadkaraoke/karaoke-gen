/**
 * Tests for API client
 */

import { api, ApiError, BackendUnavailableError, extractErrorMessage } from "../api"

// Mock fetch globally
global.fetch = jest.fn()

describe("API Client", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe("listJobs", () => {
    it("should fetch jobs successfully", async () => {
      const mockJobs = [
        { job_id: "1", status: "complete", artist: "Test Artist", title: "Test Song" },
      ]

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockJobs,
      })

      const result = await api.listJobs()
      expect(result).toEqual(mockJobs)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs"),
        expect.any(Object)
      )
    })

    it("should handle errors", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ detail: "Server error" }),
      })

      await expect(api.listJobs()).rejects.toThrow(ApiError)
    })

    it("should include query parameters", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })

      await api.listJobs({ status: "complete", limit: 10 })
      const callArgs = (global.fetch as jest.Mock).mock.calls[0][0]
      expect(callArgs).toContain("status=complete")
      expect(callArgs).toContain("limit=10")
    })

    it("should send fields=summary query param", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })

      await api.listJobs({ fields: "summary", limit: 100 })
      const callArgs = (global.fetch as jest.Mock).mock.calls[0][0]
      expect(callArgs).toContain("fields=summary")
    })

    it("should send hide_completed=true query param", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })

      await api.listJobs({ fields: "summary", hide_completed: true })
      const callArgs = (global.fetch as jest.Mock).mock.calls[0][0]
      expect(callArgs).toContain("hide_completed=true")
    })

    it("should not send hide_completed when undefined", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })

      await api.listJobs({ fields: "summary" })
      const callArgs = (global.fetch as jest.Mock).mock.calls[0][0]
      expect(callArgs).not.toContain("hide_completed")
    })

    it("should handle summary response with partial job data", async () => {
      const summaryJobs = [
        {
          job_id: "abc",
          status: "pending",
          progress: 0,
          created_at: "2026-01-01T00:00:00",
          artist: "Test",
          title: "Song",
          state_data: { brand_code: "NKP" },
          file_urls: { finals: {} },
        },
      ]

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => summaryJobs,
      })

      const result = await api.listJobs({ fields: "summary" })
      expect(result).toHaveLength(1)
      expect(result[0].job_id).toBe("abc")
      // Summary jobs may have missing optional fields
      expect(result[0].timeline).toBeUndefined()
      expect(result[0].review_token).toBeUndefined()
    })
  })

  describe("getJob", () => {
    it("should fetch a single job", async () => {
      const mockJob = { job_id: "123", status: "complete" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockJob,
      })

      const result = await api.getJob("123")
      expect(result).toEqual(mockJob)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/123"),
        expect.any(Object)
      )
    })
  })

  describe("uploadJob", () => {
    it("should upload a file and create a job", async () => {
      const mockFile = new File(["test"], "test.mp3", { type: "audio/mpeg" })
      const mockResponse = { status: "success", job_id: "123" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.uploadJob(mockFile, "Artist", "Title")
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/upload"),
        expect.objectContaining({ method: "POST" })
      )
    })
  })

  describe("createJobWithUploadUrls", () => {
    it("should create a job and return signed upload URLs", async () => {
      const mockResponse = {
        status: "success",
        job_id: "job-456",
        message: "Job created. Upload files.",
        upload_urls: [
          { file_type: "audio", gcs_path: "uploads/job-456/audio.mp3", upload_url: "https://storage.googleapis.com/signed-url", content_type: "audio/mpeg" },
        ],
        server_version: "1.0.0",
      }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.createJobWithUploadUrls(
        "Artist", "Title",
        [{ filename: "test.mp3", content_type: "audio/mpeg", file_type: "audio" }],
        { is_private: true },
      )

      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/create-with-upload-urls"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        })
      )

      // Verify request body
      const call = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(call[1].body)
      expect(body.artist).toBe("Artist")
      expect(body.title).toBe("Title")
      expect(body.files).toHaveLength(1)
      expect(body.is_private).toBe(true)
    })
  })

  describe("uploadToSignedUrl", () => {
    let mockXhr: {
      open: jest.Mock
      setRequestHeader: jest.Mock
      send: jest.Mock
      upload: { onprogress: ((e: any) => void) | null }
      onload: (() => void) | null
      onerror: (() => void) | null
      status: number
      statusText: string
    }

    beforeEach(() => {
      mockXhr = {
        open: jest.fn(),
        setRequestHeader: jest.fn(),
        send: jest.fn(),
        upload: { onprogress: null },
        onload: null,
        onerror: null,
        status: 200,
        statusText: "OK",
      }
      global.XMLHttpRequest = jest.fn(() => mockXhr) as any
    })

    it("should PUT file to signed URL", async () => {
      const file = new File(["test"], "test.mp3", { type: "audio/mpeg" })
      mockXhr.send.mockImplementation(() => {
        mockXhr.onload?.()
      })

      await api.uploadToSignedUrl("https://storage.googleapis.com/signed", file, "audio/mpeg")

      expect(mockXhr.open).toHaveBeenCalledWith("PUT", "https://storage.googleapis.com/signed", true)
      expect(mockXhr.setRequestHeader).toHaveBeenCalledWith("Content-Type", "audio/mpeg")
      expect(mockXhr.send).toHaveBeenCalledWith(file)
    })

    it("should report progress via callback", async () => {
      const file = new File(["test"], "test.mp3", { type: "audio/mpeg" })
      const onProgress = jest.fn()

      mockXhr.send.mockImplementation(() => {
        // Simulate progress event
        mockXhr.upload.onprogress?.({ lengthComputable: true, loaded: 500, total: 1000 })
        mockXhr.onload?.()
      })

      await api.uploadToSignedUrl("https://storage.googleapis.com/signed", file, "audio/mpeg", onProgress)

      expect(onProgress).toHaveBeenCalledWith(500, 1000)
    })

    it("should reject on non-2xx status", async () => {
      const file = new File(["test"], "test.mp3", { type: "audio/mpeg" })
      mockXhr.status = 403
      mockXhr.statusText = "Forbidden"
      mockXhr.send.mockImplementation(() => {
        mockXhr.onload?.()
      })

      await expect(
        api.uploadToSignedUrl("https://storage.googleapis.com/signed", file, "audio/mpeg")
      ).rejects.toThrow("Upload failed: 403 Forbidden")
    })

    it("should reject on network error", async () => {
      const file = new File(["test"], "test.mp3", { type: "audio/mpeg" })
      mockXhr.send.mockImplementation(() => {
        mockXhr.onerror?.()
      })

      await expect(
        api.uploadToSignedUrl("https://storage.googleapis.com/signed", file, "audio/mpeg")
      ).rejects.toThrow("network error")
    })
  })

  describe("completeJobUpload", () => {
    it("should POST uploaded file types", async () => {
      const mockResponse = { status: "success", message: "Processing started" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.completeJobUpload("job-789", ["audio"])

      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/job-789/uploads-complete"),
        expect.objectContaining({ method: "POST" })
      )

      const call = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(call[1].body)
      expect(body.uploaded_files).toEqual(["audio"])
    })

    it("retries on a transient network failure and then succeeds", async () => {
      const mockResponse = { status: "success", message: "Processing started" }

      ;(global.fetch as jest.Mock)
        .mockRejectedValueOnce(new TypeError("Failed to fetch"))
        .mockResolvedValueOnce({ ok: true, json: async () => mockResponse })

      const result = await api.completeJobUpload("job-789", ["audio"])

      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledTimes(2)
    })

    it("gives up after 3 network failures with BackendUnavailableError (preserving the cause)", async () => {
      const networkError = new TypeError("Failed to fetch")
      ;(global.fetch as jest.Mock).mockRejectedValue(networkError)

      // apiFetch maps a network-level failure to a typed BackendUnavailableError
      // (which drives the app-wide connectivity banner) while keeping the original
      // error as `.cause`, so nothing is silently swallowed.
      const err = await api
        .completeJobUpload("job-789", ["audio"])
        .catch((e) => e)
      expect(err).toBeInstanceOf(BackendUnavailableError)
      expect(err.cause).toBe(networkError)
      expect(global.fetch).toHaveBeenCalledTimes(3)
    })

    it("does NOT retry on an HTTP error response (deterministic)", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: async () => ({ detail: "Job is not in pending state" }),
      })

      await expect(api.completeJobUpload("job-789", ["audio"])).rejects.toBeInstanceOf(
        ApiError
      )
      // A 4xx is not a transient failure — it must not be retried.
      expect(global.fetch).toHaveBeenCalledTimes(1)
    })
  })

  describe("uploadInstrumentalForReview", () => {
    it("gets a signed URL, PUTs to GCS, then finalizes with the gcs_path", async () => {
      const xhrs: any[] = []
      global.XMLHttpRequest = jest.fn(() => {
        const xhr: any = {
          open: jest.fn(), setRequestHeader: jest.fn(), upload: { onprogress: null }, status: 200, statusText: "OK",
          send: jest.fn(function (this: any) { this.onload() }),
        }
        xhrs.push(xhr)
        return xhr
      }) as any
      ;(global.fetch as jest.Mock)
        .mockResolvedValueOnce({ ok: true, json: async () => ({ upload_url: "https://gcs/inst", gcs_path: "jobs/j1/uploads/custom_instrumental_source.wav", content_type: "audio/wav" }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "success", duration_seconds: 200, message: "ok", audio_url: "https://gcs/signed-flac" }) })

      const file = new File(["x".repeat(10)], "inst.wav", { type: "audio/wav" })
      const progress = jest.fn()
      const result = await api.uploadInstrumentalForReview("j1", file, progress)

      const [urlCall, completeCall] = (global.fetch as jest.Mock).mock.calls
      expect(urlCall[0]).toContain("/api/jobs/j1/instrumental-upload-url")
      expect(JSON.parse(urlCall[1].body)).toEqual({ filename: "inst.wav", content_type: "audio/wav" })
      expect(xhrs[0].open).toHaveBeenCalledWith("PUT", "https://gcs/inst", true)
      expect(completeCall[0]).toContain("/api/jobs/j1/instrumental-upload-complete")
      expect(JSON.parse(completeCall[1].body)).toEqual({ gcs_path: "jobs/j1/uploads/custom_instrumental_source.wav" })
      expect(result.audio_url).toBe("https://gcs/signed-flac")
      expect(progress).toHaveBeenCalledWith(expect.objectContaining({ phase: "finalizing" }))
    })

    it("surfaces the backend's duration-mismatch message from finalize", async () => {
      global.XMLHttpRequest = jest.fn(() => ({
        open: jest.fn(), setRequestHeader: jest.fn(), upload: {}, status: 200, statusText: "OK",
        send: jest.fn(function (this: any) { this.onload() }),
      })) as any
      ;(global.fetch as jest.Mock)
        .mockResolvedValueOnce({ ok: true, json: async () => ({ upload_url: "u", gcs_path: "p", content_type: "audio/wav" }) })
        .mockResolvedValueOnce({ ok: false, status: 400, json: async () => ({ detail: "Duration mismatch: 185.0s vs 200.0s" }) })

      await expect(api.uploadInstrumentalForReview("j1", new File(["x"], "i.wav"))).rejects.toThrow("Duration mismatch")
    })
  })

  describe("createJobFromUploadedAudio", () => {
    function mockXhrFactory() {
      const xhrs: any[] = []
      global.XMLHttpRequest = jest.fn(() => {
        const xhr: any = {
          open: jest.fn(),
          setRequestHeader: jest.fn(),
          send: jest.fn().mockImplementation(function (this: any, file: File) {
            this.upload.onprogress?.({ lengthComputable: true, loaded: file.size, total: file.size })
            this.onload?.()
          }),
          upload: { onprogress: null },
          onload: null,
          onerror: null,
          status: 200,
          statusText: "OK",
        }
        xhrs.push(xhr)
        return xhr
      }) as any
      return xhrs
    }

    function mockCreate(jobId: string, uploadUrls: any[]) {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "success", job_id: jobId, message: "Job created", upload_urls: uploadUrls, server_version: "1.0.0" }),
      })
    }

    it("always uses the signed URL flow, even for small files", async () => {
      const xhrs = mockXhrFactory()
      const smallFile = new File(["x".repeat(100)], "small.mp3", { type: "audio/mpeg" })
      mockCreate("signed-123", [
        { file_type: "audio", gcs_path: "uploads/signed-123/audio/small.mp3", upload_url: "https://storage.googleapis.com/a", content_type: "audio/mpeg" },
      ])
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => ({ status: "success", message: "ok" }) })

      const progress = jest.fn()
      const result = await api.createJobFromUploadedAudio(smallFile, "Artist", "Title", { is_private: true, requires_audio_edit: true }, progress)

      expect(result.job_id).toBe("signed-123")
      const createBody = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body)
      expect(createBody.files).toEqual([{ filename: "small.mp3", content_type: "audio/mpeg", file_type: "audio" }])
      expect(createBody.requires_audio_edit).toBe(true)
      expect(createBody.existing_instrumental).toBeUndefined()
      expect(xhrs[0].open).toHaveBeenCalledWith("PUT", "https://storage.googleapis.com/a", true)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/signed-123/uploads-complete"),
        expect.objectContaining({ body: JSON.stringify({ uploaded_files: ["audio"] }) }),
      )
      expect(progress).toHaveBeenCalledWith(expect.objectContaining({ phase: "creating" }))
      expect(progress).toHaveBeenCalledWith(expect.objectContaining({ phase: "uploading" }))
      expect(progress).toHaveBeenCalledWith(expect.objectContaining({ phase: "finalizing" }))
    })

    it("uploads an instrumental alongside the mix with aggregate progress", async () => {
      const xhrs = mockXhrFactory()
      const mix = new File(["m".repeat(300)], "mix.wav", { type: "audio/wav" })
      const inst = new File(["i".repeat(100)], "inst.wav", { type: "audio/wav" })
      mockCreate("inst-1", [
        { file_type: "audio", upload_url: "https://gcs/mix", content_type: "audio/wav" },
        { file_type: "existing_instrumental", upload_url: "https://gcs/inst", content_type: "audio/wav" },
      ])
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => ({ status: "success", message: "ok" }) })

      const progress = jest.fn()
      await api.createJobFromUploadedAudio(mix, "A", "T", { is_private: true, instrumentalFile: inst }, progress)

      const createBody = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body)
      expect(createBody.existing_instrumental).toBe(true)
      expect(createBody.files.map((f: any) => f.file_type)).toEqual(["audio", "existing_instrumental"])
      expect(xhrs.map(x => x.open.mock.calls[0][1])).toEqual(["https://gcs/mix", "https://gcs/inst"])
      expect(progress).toHaveBeenCalledWith(
        expect.objectContaining({ phase: "uploading", loaded: 400, total: 400, fileName: "inst.wav", fileIndex: 2, fileCount: 2 })
      )
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/inst-1/uploads-complete"),
        expect.objectContaining({ body: JSON.stringify({ uploaded_files: ["audio", "existing_instrumental"] }) }),
      )
    })

    it("throws if an upload URL is missing", async () => {
      const file = new File(["x"], "a.wav", { type: "audio/wav" })
      mockCreate("bad-789", [])

      await expect(api.createJobFromUploadedAudio(file, "Artist", "Title")).rejects.toThrow(
        "No upload URL returned for audio"
      )
    })
  })

  describe("searchAudio", () => {
    it("should search for audio", async () => {
      const mockResponse = {
        status: "success",
        job_id: "123",
        results: [],
        total_results: 0,
      }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.searchAudio("Artist", "Title")
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/audio-search/search"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        })
      )
    })
  })

  describe("getJobLogs", () => {
    it("should fetch job logs", async () => {
      const mockLogs = [
        { timestamp: "2025-01-01T00:00:00Z", level: "INFO", message: "Test log" },
      ]

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ logs: mockLogs }),
      })

      const result = await api.getJobLogs("123")
      expect(result).toEqual(mockLogs)
      const callArgs = (global.fetch as jest.Mock).mock.calls[0][0]
      expect(callArgs).toContain("/api/jobs/123/logs")
    })

    it("should handle missing logs field", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      })

      const result = await api.getJobLogs("123")
      expect(result).toEqual([])
    })
  })

  describe("cancelJob", () => {
    it("should cancel a job", async () => {
      const mockResponse = { status: "success", message: "Job cancelled" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.cancelJob("123", "User requested")
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/123/cancel"),
        expect.objectContaining({ method: "POST" })
      )
    })
  })

  describe("retryJob", () => {
    it("should retry a failed job", async () => {
      const mockResponse = { status: "success", job_id: "123", message: "Job retried" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.retryJob("123")
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/123/retry"),
        expect.objectContaining({ method: "POST" })
      )
    })
  })

  describe("completeReview", () => {
    it("should complete lyrics review", async () => {
      const mockResponse = { status: "success", job_status: "review_complete", message: "Review completed" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.completeReview("123")
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/123/complete-review"),
        expect.objectContaining({ method: "POST" })
      )
    })
  })

  describe("selectInstrumental", () => {
    it("should select instrumental", async () => {
      const mockResponse = { status: "success", job_status: "instrumental_selected", selection: "clean", message: "Instrumental selected" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.selectInstrumental("123", "clean")
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/123/select-instrumental"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        })
      )
    })
  })

  describe("selectAudioResult", () => {
    it("should select audio search result", async () => {
      const mockResponse = { status: "success", message: "Audio selected" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      const result = await api.selectAudioResult("123", 0)
      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/audio-search/123/select"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        })
      )
    })

    it("should send overrides in request body when provided", async () => {
      const mockResponse = { status: "success", message: "Audio selected" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      await api.selectAudioResult("456", 2, {
        is_private: true,
        display_artist: "Custom Artist",
        display_title: "Custom Title",
      })

      const fetchCall = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(fetchCall[1].body)
      expect(body).toEqual({
        selection_index: 2,
        is_private: true,
        display_artist: "Custom Artist",
        display_title: "Custom Title",
      })
    })

    it("should not include undefined overrides in request body", async () => {
      const mockResponse = { status: "success", message: "Audio selected" }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      })

      await api.selectAudioResult("789", 0, {
        is_private: true,
      })

      const fetchCall = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(fetchCall[1].body)
      expect(body.selection_index).toBe(0)
      expect(body.is_private).toBe(true)
      expect(body.display_artist).toBeUndefined()
      expect(body.display_title).toBeUndefined()
    })
  })
})

describe("createLyricsReviewApiClient", () => {
  const { createLyricsReviewApiClient } = require("../api")

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe("addLyrics", () => {
    it("should call correct endpoint: /api/review/{jobId}/add-lyrics", async () => {
      const mockCorrectionData = {
        corrected_segments: [],
        metadata: {},
        corrections: [],
        reference_lyrics: { genius: { segments: [] } },
      }

      // Backend returns { status: "success", data: CorrectionData }
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "success", data: mockCorrectionData }),
      })

      const client = createLyricsReviewApiClient("job123")
      const result = await client.addLyrics("genius", "Some lyrics text")

      // Verify the EXACT endpoint path - this was the bug: was calling /api/jobs/{id}/lyrics
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/review/job123/add-lyrics"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        })
      )

      // Verify request body (force is sent as a string — the endpoint body is Dict[str, str])
      const call = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(call[1].body)
      expect(body).toEqual({ source: "genius", lyrics: "Some lyrics text", force: "false" })

      // Verify the client returns the full result envelope
      expect(result).toEqual({ status: "success", data: mockCorrectionData })
    })

    it("should send force='true' when force-adding", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "success", data: {} }),
      })

      const client = createLyricsReviewApiClient("job123")
      await client.addLyrics("manual", "Some lyrics text", true)

      const call = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(call[1].body)
      expect(body).toEqual({ source: "manual", lyrics: "Some lyrics text", force: "true" })
    })

    it("should surface rejected status and rejection details", async () => {
      const rejection = { relevance: 0.013, matched_words: 3, total_words: 230 }
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "rejected", rejection, data: { reference_lyrics: {} } }),
      })

      const client = createLyricsReviewApiClient("job123")
      const result = await client.addLyrics("manual", "Wrong song lyrics")

      expect(result.status).toBe("rejected")
      expect(result.rejection).toEqual(rejection)
    })
  })

  describe("updateHandlers", () => {
    it("should call correct endpoint: /api/review/{jobId}/handlers with POST", async () => {
      const mockCorrectionData = {
        corrected_segments: [],
        metadata: { enabled_handlers: ["handler1"] },
        corrections: [],
      }

      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => mockCorrectionData,
      })

      const client = createLyricsReviewApiClient("job123")
      await client.updateHandlers(["handler1", "handler2"])

      // Verify the EXACT endpoint path and method - was calling PATCH /api/jobs/{id}/handlers
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/review/job123/handlers"),
        expect.objectContaining({
          method: "POST", // Backend expects POST, not PATCH
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        })
      )

      // Verify request body
      const call = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(call[1].body)
      expect(body).toEqual({ enabled_handlers: ["handler1", "handler2"] })
    })
  })

  describe("submitCorrections", () => {
    it("should wrap CorrectionData with lines alias for backend validation", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "success" }),
      })

      const client = createLyricsReviewApiClient("job123")
      const mockCorrectionData = {
        corrected_segments: [
          { id: "seg1", text: "Hello", words: [], start_time: 0, end_time: 1 },
        ],
        metadata: { total_words: 1 },
        corrections: [],
        original_segments: [],
        reference_lyrics: {},
        anchor_sequences: [],
        gap_sequences: [],
        resized_segments: [],
        corrections_made: 0,
        confidence: 0.9,
        correction_steps: [],
        word_id_map: {},
        segment_id_map: {},
      }

      await client.submitCorrections(mockCorrectionData)

      // Verify the payload structure sent to the backend
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/job123/corrections"),
        expect.objectContaining({
          method: "POST",
          body: expect.any(String),
        })
      )

      // Parse the body to verify structure
      const call = (global.fetch as jest.Mock).mock.calls[0]
      const body = JSON.parse(call[1].body)

      // Backend expects: { corrections: { lines: [...], metadata: {...}, ... } }
      expect(body).toHaveProperty("corrections")
      expect(body.corrections).toHaveProperty("lines") // Required by backend validator
      expect(body.corrections).toHaveProperty("metadata") // Required by backend validator
      expect(body.corrections).toHaveProperty("corrected_segments") // Needed by render worker
      expect(body.corrections.lines).toEqual(mockCorrectionData.corrected_segments)
    })
  })
})

describe("extractErrorMessage", () => {
  const fallback = "Default error message"

  it("should extract string detail field", () => {
    expect(extractErrorMessage({ detail: "Error from detail" }, fallback)).toBe("Error from detail")
  })

  it("should extract string message field", () => {
    expect(extractErrorMessage({ message: "Error from message" }, fallback)).toBe("Error from message")
  })

  it("should extract string error field", () => {
    expect(extractErrorMessage({ error: "Error from error field" }, fallback)).toBe("Error from error field")
  })

  it("should prefer detail over message", () => {
    expect(extractErrorMessage({ detail: "Detail", message: "Message" }, fallback)).toBe("Detail")
  })

  it("should extract nested detail.message", () => {
    expect(extractErrorMessage({ detail: { message: "Nested message" } }, fallback)).toBe("Nested message")
  })

  it("should extract nested detail.error", () => {
    expect(extractErrorMessage({ detail: { error: "Nested error" } }, fallback)).toBe("Nested error")
  })

  it("should stringify object detail", () => {
    const objDetail = { code: "ERR_001", info: "More info" }
    expect(extractErrorMessage({ detail: objDetail }, fallback)).toBe(JSON.stringify(objDetail))
  })

  it("should return fallback for null data", () => {
    expect(extractErrorMessage(null, fallback)).toBe(fallback)
  })

  it("should return fallback for undefined data", () => {
    expect(extractErrorMessage(undefined, fallback)).toBe(fallback)
  })

  it("should return fallback for empty object", () => {
    expect(extractErrorMessage({}, fallback)).toBe(fallback)
  })

  it("should return fallback for numeric detail (not a string)", () => {
    // Numbers are not extracted - only strings
    expect(extractErrorMessage({ detail: 123 }, fallback)).toBe(fallback)
  })

  it("should handle [object Object] case that caused the original bug", () => {
    // This is the case that caused [object Object] to be displayed
    const problematicData = { detail: { some: "object" } }
    const result = extractErrorMessage(problematicData, fallback)
    expect(result).not.toBe("[object Object]")
    expect(result).toBe('{"some":"object"}')
  })
})
