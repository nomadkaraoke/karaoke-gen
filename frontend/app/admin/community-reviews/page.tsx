"use client"

import { useCallback, useEffect, useState } from "react"
import { adminApi } from "@/lib/api"
import type { CommunityReviewItem } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  ListChecks, Youtube, Loader2, Sparkles, XCircle, Clock, RefreshCw,
} from "lucide-react"

type Action = "make" | "reject" | "keep"

export default function CommunityReviewsPage() {
  const [reviews, setReviews] = useState<CommunityReviewItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actioningId, setActioningId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await adminApi.listCommunityReviews()
      setReviews(res.reviews)
    } catch {
      setError("Failed to load community reviews.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const doAction = async (id: string, action: Action) => {
    if (action === "reject" && !confirm("Reject this request and email its up-voters the existing version?")) {
      return
    }
    setActioningId(id)
    setNotice(null)
    setError(null)
    try {
      const res = await adminApi.actionCommunityReview(id, action)
      setNotice(res.message)
      setReviews((prev) => prev.filter((r) => r.id !== id))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed.")
    } finally {
      setActioningId(null)
    }
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ListChecks className="w-6 h-6" /> Community Reviews
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Requests-board picks the daily picker flagged because a community karaoke
            version already exists. Choose to make ours anyway, reject (and notify
            up-voters of the existing one), or keep it on the board.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-1 ${loading ? "animate-spin" : ""}`} /> Refresh
        </Button>
      </div>

      {notice && (
        <div className="rounded-lg px-3 py-2 text-sm bg-green-500/10 text-green-600 border border-green-500/30">
          {notice}
        </div>
      )}
      {error && (
        <div className="rounded-lg px-3 py-2 text-sm bg-red-500/10 text-red-500 border border-red-500/30">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-32 w-full" />)}
        </div>
      ) : reviews.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            No picks awaiting review 🎉
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {reviews.map((r) => {
            const tracks = r.community_versions?.tracks ?? []
            const busy = actioningId === r.id
            return (
              <Card key={r.id}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center justify-between gap-3">
                    <span className="truncate">
                      {r.artist} — {r.title}
                    </span>
                    <span className="flex items-center gap-2 shrink-0">
                      <Badge variant="secondary">{r.vote_count} votes</Badge>
                      <Badge variant="outline">{r.upvoter_count} up-voters</Badge>
                    </span>
                  </CardTitle>
                  <p className="text-xs text-muted-foreground">
                    Requested by {r.submitted_by}
                    {r.community_checked_at ? ` · checked ${new Date(r.community_checked_at).toLocaleString()}` : ""}
                  </p>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground">Existing community versions:</p>
                    {tracks.length > 0 ? (
                      tracks.slice(0, 5).map((tr, i) => (
                        <a
                          key={i}
                          href={tr.youtube_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-2 text-sm text-blue-500 hover:underline"
                        >
                          <Youtube className="w-4 h-4 text-red-500 shrink-0" />
                          <span className="truncate">{tr.brand_name}</span>
                        </a>
                      ))
                    ) : r.community_versions?.best_youtube_url ? (
                      <a
                        href={r.community_versions.best_youtube_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-2 text-sm text-blue-500 hover:underline"
                      >
                        <Youtube className="w-4 h-4 text-red-500 shrink-0" /> Watch on YouTube
                      </a>
                    ) : (
                      <p className="text-sm text-muted-foreground">(no links captured)</p>
                    )}
                  </div>

                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button size="sm" onClick={() => doAction(r.id, "make")} disabled={busy}>
                      {busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Sparkles className="w-4 h-4 mr-1" />}
                      Make ours anyway
                    </Button>
                    <Button size="sm" variant="destructive" onClick={() => doAction(r.id, "reject")} disabled={busy}>
                      <XCircle className="w-4 h-4 mr-1" /> Reject &amp; notify voters
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => doAction(r.id, "keep")} disabled={busy}>
                      <Clock className="w-4 h-4 mr-1" /> Keep on board
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
