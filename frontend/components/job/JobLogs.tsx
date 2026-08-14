"use client"

import { useEffect, useState } from "react"
import { api, JobLog } from "@/lib/api"
import { Loader2, AlertCircle } from "lucide-react"
import { parseServerDate } from "@/lib/utils"

interface JobLogsProps {
  jobId: string
  limit?: number
}

export function JobLogs({ jobId, limit = 50 }: JobLogsProps) {
  const [logs, setLogs] = useState<JobLog[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    loadLogs()
  }, [jobId])

  async function loadLogs() {
    setIsLoading(true)
    setError("")
    try {
      const data = await api.getJobLogs(jobId, limit)
      setLogs(data)
    } catch (err) {
      console.error("Failed to load logs:", err)
      setError("Failed to load logs")
    } finally {
      setIsLoading(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-4" style={{ color: 'var(--text-muted)' }}>
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading logs...
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center py-4 text-red-400 text-sm">
        <AlertCircle className="w-4 h-4 mr-2" />
        {error}
      </div>
    )
  }

  if (logs.length === 0) {
    return (
      <div className="text-center py-4 text-sm" style={{ color: 'var(--text-muted)' }}>
        No logs available
      </div>
    )
  }

  return (
    <div className="rounded border p-3 max-h-64 overflow-y-auto" style={{ backgroundColor: 'var(--bg)', borderColor: 'var(--card-border)' }}>
      <div className="space-y-1 font-mono text-xs">
        {logs.map((log, index) => (
          <div
            key={index}
            className={`flex gap-2 ${
              log.level === "ERROR" ? "text-red-400" :
              log.level === "WARNING" ? "text-yellow-400" :
              log.level === "INFO" ? "text-blue-400" :
              ""
            }`}
            style={log.level !== "ERROR" && log.level !== "WARNING" && log.level !== "INFO" ? { color: 'var(--text-muted)' } : undefined}
          >
            <span className="shrink-0" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
              {parseServerDate(log.timestamp).toLocaleTimeString()}
            </span>
            <span className="shrink-0 font-semibold w-12">
              {log.level}
            </span>
            <span className="break-all">{log.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

