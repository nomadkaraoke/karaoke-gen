"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"

export default function CrashTestPage() {
  const [mode, setMode] = useState<"sync" | "async" | "render" | null>(null)

  if (mode === "render") {
    throw new Error("synthetic render crash")
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Crash test</h1>
        <p className="text-muted-foreground mt-1">
          Fires intentional client-side errors to exercise the crash-reporting pipeline end-to-end.
          Each button triggers a different error flavour so you can verify Discord alerts fire and
          patterns land in Firestore with <code className="font-mono text-xs">service=frontend</code>.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => {
            setTimeout(() => {
              throw new Error("synthetic async crash")
            }, 0)
          }}
        >
          Async throw (setTimeout)
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            void Promise.reject(new Error("synthetic unhandled rejection"))
          }}
        >
          Unhandled rejection
        </Button>
        <Button variant="destructive" onClick={() => setMode("render")}>
          Render-time throw
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        After clicking, check the browser console and the Discord error channel. The async and
        rejection variants should NOT show a visible error UI (they&apos;re caught by the global
        handlers). The render-time throw should render the <code>CrashReport</code> card via the
        Next.js <code>error.tsx</code> boundary.
      </p>
    </div>
  )
}
