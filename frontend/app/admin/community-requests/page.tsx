"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { adminApi } from "@/lib/api"
import type { CommunityRequestItem } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableHeader, TableBody, TableHead, TableRow, TableCell,
} from "@/components/ui/table"
import { History, Youtube, ExternalLink, RefreshCw } from "lucide-react"

// Status → badge styling. Mirrors the request lifecycle in backend/models/song_request.py.
const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  open: "secondary",
  queued: "outline",
  in_progress: "outline",
  published: "default",
  rejected: "destructive",
  stalled: "destructive",
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
}

export default function CommunityRequestsPage() {
  const [requests, setRequests] = useState<CommunityRequestItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await adminApi.listCommunityRequests()
      setRequests(res.requests)
    } catch {
      setError("Failed to load community requests.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of requests) c[r.status] = (c[r.status] ?? 0) + 1
    return c
  }, [requests])

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <History className="w-6 h-6" /> Community Requests
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Every song requested via the public voting board
            (<a href="https://requests.nomadkaraoke.com" target="_blank" rel="noopener noreferrer"
              className="text-blue-500 hover:underline">requests.nomadkaraoke.com</a>) —
            who asked, how it was voted, which job made it, and where it landed on YouTube.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-1 ${loading ? "animate-spin" : ""}`} /> Refresh
        </Button>
      </div>

      {error && (
        <div className="rounded-lg px-3 py-2 text-sm bg-red-500/10 text-red-500 border border-red-500/30">
          {error}
        </div>
      )}

      {!loading && requests.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge variant="outline">{requests.length} total</Badge>
          {Object.entries(counts).map(([status, n]) => (
            <Badge key={status} variant={STATUS_VARIANT[status] ?? "outline"}>
              {n} {status.replace("_", " ")}
            </Badge>
          ))}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
        </div>
      ) : requests.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            No community requests yet.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Song</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Requester</TableHead>
                  <TableHead className="text-right">Votes</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead>Job</TableHead>
                  <TableHead>Video</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">
                      {r.artist} — {r.title}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[r.status] ?? "outline"}>
                        {r.status.replace("_", " ")}
                      </Badge>
                      {r.review_state ? (
                        <Badge variant="outline" className="ml-1">{r.review_state}</Badge>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-sm">
                      <span className="truncate">{r.submitted_by}</span>
                      {r.owner_email && r.owner_email !== r.submitted_by ? (
                        <span className="block text-xs text-muted-foreground">owner: {r.owner_email}</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right">{r.vote_count}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{fmtDate(r.created_at)}</TableCell>
                    <TableCell>
                      {r.job_id ? (
                        <a
                          href={`/app/jobs#/${r.job_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-sm text-blue-500 hover:underline"
                        >
                          {r.job_id.slice(0, 8)} <ExternalLink className="w-3 h-3" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground text-sm">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {r.youtube_url ? (
                        <a
                          href={r.youtube_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-sm text-blue-500 hover:underline"
                        >
                          <Youtube className="w-4 h-4 text-red-500" /> Watch
                        </a>
                      ) : (
                        <span className="text-muted-foreground text-sm">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
