import { ApiError } from "../api-error"
import {
  durationsMismatch,
  getAudioFileDuration,
  isNetworkUploadError,
  putFileToSignedUrl,
  uploadFilesToSignedUrls,
} from "../upload"

function installXhr(behaviour: (xhr: any, file: File) => void) {
  const xhrs: any[] = []
  global.XMLHttpRequest = jest.fn(() => {
    const xhr: any = {
      open: jest.fn(),
      setRequestHeader: jest.fn(),
      upload: { onprogress: null },
      status: 200,
      statusText: "OK",
    }
    xhr.send = jest.fn((file: File) => behaviour(xhr, file))
    xhrs.push(xhr)
    return xhr
  }) as any
  return xhrs
}

describe("putFileToSignedUrl", () => {
  it("PUTs with the content type and reports progress", async () => {
    const xhrs = installXhr((xhr, file) => {
      xhr.upload.onprogress({ lengthComputable: true, loaded: 2, total: file.size })
      xhr.onload()
    })
    const onProgress = jest.fn()
    await putFileToSignedUrl("https://gcs/x", new File(["abcd"], "a.wav"), "audio/wav", onProgress)

    expect(xhrs[0].open).toHaveBeenCalledWith("PUT", "https://gcs/x", true)
    expect(xhrs[0].setRequestHeader).toHaveBeenCalledWith("Content-Type", "audio/wav")
    expect(onProgress).toHaveBeenCalledWith(2, 4)
  })

  it("rejects with the HTTP status on a non-2xx response", async () => {
    installXhr((xhr) => { xhr.status = 403; xhr.statusText = "Forbidden"; xhr.onload() })
    const err = await putFileToSignedUrl("u", new File(["a"], "a"), "x").catch(e => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(403)
  })

  it("rejects with status 0 on a network error", async () => {
    installXhr((xhr) => xhr.onerror())
    const err = await putFileToSignedUrl("u", new File(["a"], "a"), "x").catch(e => e)
    expect(err.status).toBe(0)
    expect(isNetworkUploadError(err)).toBe(true)
  })
})

describe("uploadFilesToSignedUrls", () => {
  it("uploads in order and reports aggregate progress across files", async () => {
    installXhr((xhr, file) => {
      xhr.upload.onprogress({ lengthComputable: true, loaded: file.size, total: file.size })
      xhr.onload()
    })
    const a = new File(["aaa"], "mix.wav")
    const b = new File(["b"], "inst.wav")
    const progress = jest.fn()
    await uploadFilesToSignedUrls(
      [{ file: a, url: "u1", contentType: "audio/wav" }, { file: b, url: "u2", contentType: "audio/wav" }],
      progress,
    )
    const calls = progress.mock.calls.map(([p]) => [p.loaded, p.total, p.fileName, p.fileIndex, p.fileCount])
    expect(calls).toEqual([
      [0, 4, "mix.wav", 1, 2],
      [3, 4, "mix.wav", 1, 2],
      [3, 4, "inst.wav", 2, 2],
      [4, 4, "inst.wav", 2, 2],
    ])
  })

  it("stops at the first failed file", async () => {
    const xhrs = installXhr((xhr) => xhr.onerror())
    await expect(
      uploadFilesToSignedUrls([
        { file: new File(["a"], "a"), url: "u1", contentType: "x" },
        { file: new File(["b"], "b"), url: "u2", contentType: "x" },
      ])
    ).rejects.toBeInstanceOf(ApiError)
    expect(xhrs).toHaveLength(1)
  })
})

describe("durationsMismatch", () => {
  it("flags differences over half a second only when both are known", () => {
    expect(durationsMismatch(180, 180.4)).toBe(false)
    expect(durationsMismatch(180, 180.6)).toBe(true)
    expect(durationsMismatch(null, 100)).toBe(false)
    expect(durationsMismatch(100, null)).toBe(false)
  })
})

describe("isNetworkUploadError", () => {
  it("distinguishes network failures from HTTP errors", () => {
    expect(isNetworkUploadError(new ApiError("x", 0))).toBe(true)
    expect(isNetworkUploadError(new ApiError("Duration mismatch", 400))).toBe(false)
    expect(isNetworkUploadError(new TypeError("Failed to fetch"))).toBe(true)
    expect(isNetworkUploadError(new Error("boom"))).toBe(false)
  })
})

describe("getAudioFileDuration", () => {
  const realCreate = document.createElement.bind(document)
  beforeEach(() => {
    global.URL.createObjectURL = jest.fn(() => "blob:x")
    global.URL.revokeObjectURL = jest.fn()
  })
  afterEach(() => jest.restoreAllMocks())

  function stubAudio(trigger: (el: any) => void) {
    jest.spyOn(document, "createElement").mockImplementation((tag: string) => {
      if (tag !== "audio") return realCreate(tag)
      const el: any = { removeAttribute: jest.fn() }
      Object.defineProperty(el, "src", { set() { setTimeout(() => trigger(el), 0) } })
      return el
    })
  }

  it("resolves the metadata duration", async () => {
    stubAudio((el) => { el.duration = 212.5; el.onloadedmetadata() })
    await expect(getAudioFileDuration(new File(["a"], "a.wav"))).resolves.toBe(212.5)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x")
  })

  it("resolves null when the browser can't decode it", async () => {
    stubAudio((el) => el.onerror())
    await expect(getAudioFileDuration(new File(["a"], "a.aiff"))).resolves.toBeNull()
  })

  it("resolves null on timeout", async () => {
    stubAudio(() => {})
    await expect(getAudioFileDuration(new File(["a"], "a.wav"), 10)).resolves.toBeNull()
  })
})
