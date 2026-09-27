"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import type { UploadProgress } from "@/lib/upload"
import { addBreadcrumb } from "@/lib/diagnostics"

/**
 * Warn before the page is closed / navigated away while `active` — leaving
 * mid-upload silently kills a browser → GCS upload.
 */
export function useBeforeUnloadGuard(active: boolean) {
  useEffect(() => {
    if (!active) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [active])
}

/**
 * Tracks one user-visible upload: exposes `progress` (non-null while running —
 * render <UploadProgressModal progress={progress} />) and guards against leaving
 * the page until it settles. `run` rethrows so callers keep their own error UI.
 */
export function useUploadTask() {
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const running = useRef(false)

  useBeforeUnloadGuard(progress !== null)

  const run = useCallback(
    async <T,>(task: (report: (p: UploadProgress) => void) => Promise<T>, initial?: UploadProgress): Promise<T> => {
      running.current = true
      setProgress(initial ?? { phase: "creating", loaded: 0, total: 0 })
      addBreadcrumb("upload", "start")
      // Breadcrumb phase/file changes only — progress fires many times a second.
      let lastStep = ""
      const report = (p: UploadProgress) => {
        if (!running.current) return
        const step = `${p.phase}${p.fileCount && p.fileCount > 1 ? ` file ${p.fileIndex}/${p.fileCount}` : ""}`
        if (step !== lastStep) {
          lastStep = step
          addBreadcrumb("upload", `${step} (${Math.round(p.total / 1048576)} MB)`)
        }
        setProgress(p)
      }
      try {
        const result = await task(report)
        addBreadcrumb("upload", "done")
        return result
      } catch (err) {
        addBreadcrumb("upload", `failed: ${err instanceof Error ? err.message : String(err)}`)
        throw err
      } finally {
        running.current = false
        setProgress(null)
      }
    },
    [],
  )

  return { progress, isUploading: progress !== null, run }
}
