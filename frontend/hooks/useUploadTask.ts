"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import type { UploadProgress } from "@/lib/upload"

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
      const report = (p: UploadProgress) => {
        if (running.current) setProgress(p)
      }
      try {
        return await task(report)
      } finally {
        running.current = false
        setProgress(null)
      }
    },
    [],
  )

  return { progress, isUploading: progress !== null, run }
}
