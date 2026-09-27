import { act, renderHook } from "@testing-library/react"
import { useUploadTask } from "../useUploadTask"

describe("useUploadTask", () => {
  it("exposes progress while running and clears it afterwards", async () => {
    const { result } = renderHook(() => useUploadTask())
    expect(result.current.progress).toBeNull()

    let finish!: () => void
    let promise!: Promise<string>
    act(() => {
      promise = result.current.run(async (report) => {
        report({ phase: "uploading", loaded: 5, total: 10 })
        await new Promise<void>((r) => { finish = r })
        return "job-1"
      })
    })
    expect(result.current.isUploading).toBe(true)
    expect(result.current.progress).toEqual({ phase: "uploading", loaded: 5, total: 10 })

    await act(async () => { finish(); await promise })
    await expect(promise).resolves.toBe("job-1")
    expect(result.current.progress).toBeNull()
  })

  it("warns before unload only while an upload is running", async () => {
    const add = jest.spyOn(window, "addEventListener")
    const remove = jest.spyOn(window, "removeEventListener")
    const { result } = renderHook(() => useUploadTask())
    expect(add).not.toHaveBeenCalledWith("beforeunload", expect.any(Function))

    let finish!: () => void
    let promise!: Promise<void>
    act(() => {
      promise = result.current.run(() => new Promise<void>((r) => { finish = r }))
    })
    expect(add).toHaveBeenCalledWith("beforeunload", expect.any(Function))
    const handler = add.mock.calls.find(([type]) => type === "beforeunload")![1] as (e: any) => void
    const event = { preventDefault: jest.fn(), returnValue: undefined as any }
    handler(event)
    expect(event.preventDefault).toHaveBeenCalled()

    await act(async () => { finish(); await promise })
    expect(remove).toHaveBeenCalledWith("beforeunload", handler)
  })

  it("rethrows task errors and clears progress", async () => {
    const { result } = renderHook(() => useUploadTask())
    let caught: unknown
    await act(async () => {
      await result.current.run(async () => { throw new Error("nope") }).catch((e) => { caught = e })
    })
    expect((caught as Error).message).toBe("nope")
    expect(result.current.progress).toBeNull()
  })
})

describe("useUploadTask breadcrumbs", () => {
  it("records phase/file changes (not every progress tick) for crash reports", async () => {
    const { __resetDiagnosticsForTest, getBreadcrumbs } = jest.requireActual("@/lib/diagnostics")
    __resetDiagnosticsForTest()
    const { result } = renderHook(() => useUploadTask())
    await act(async () => {
      await result.current.run(async (report) => {
        for (let i = 0; i < 5; i++) report({ phase: "uploading", loaded: i, total: 10 * 1048576, fileIndex: 1, fileCount: 2 })
        report({ phase: "uploading", loaded: 6, total: 10 * 1048576, fileIndex: 2, fileCount: 2 })
        report({ phase: "finalizing", loaded: 10, total: 10 * 1048576 })
      })
    })
    expect(getBreadcrumbs().filter((c: any) => c.category === "upload").map((c: any) => c.message)).toEqual([
      "start",
      "uploading file 1/2 (10 MB)",
      "uploading file 2/2 (10 MB)",
      "finalizing (10 MB)",
      "done",
    ])
  })
})
