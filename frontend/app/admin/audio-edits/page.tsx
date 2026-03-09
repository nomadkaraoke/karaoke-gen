"use client"

import { useEffect, useState, useCallback } from "react"
import {
  adminApi,
  AudioEditReviewSummary,
  AudioEditReviewListResponse,
  AudioEditReviewDetail,
} from "@/lib/api"
import { useAdminSettings } from "@/lib/admin-settings"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Search,
  Loader2,
  Eye,
  Code,
  ChevronLeft,
  ChevronRight,
  Copy,
  Check,
  Clock,
  Scissors,
  Play,
} from "lucide-react"
import { toast } from "sonner"

const PAGE_SIZE = 20

function formatDuration(seconds?: number): string {
  if (seconds == null || !Number.isFinite(seconds)) return "-"
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

function formatDurationChange(original?: number, current?: number): string {
  if (original == null || current == null) return ""
  const diff = current - original
  if (Math.abs(diff) < 0.5) return ""
  const sign = diff < 0 ? "-" : "+"
  const abs = Math.abs(diff)
  const mins = Math.floor(abs / 60)
  const secs = Math.floor(abs % 60)
  return `${sign}${mins}:${secs.toString().padStart(2, "0")}`
}

// ─── Edit Stack Viewer ────────────────────────────────────────────────────

function EditStackViewer({
  editStack,
}: {
  editStack: AudioEditReviewDetail["edit_stack"]
}) {
  if (editStack.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-muted-foreground">
        No edits applied.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {editStack.map((entry, idx) => (
        <div key={entry.edit_id || idx} className="border rounded-md p-3">
          <div className="flex items-center gap-2 mb-1">
            <Badge variant="outline" className="text-xs">
              {idx + 1}. {entry.operation}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {new Date(entry.timestamp).toLocaleString()}
            </span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {entry.duration_before != null && (
              <span className="text-muted-foreground text-xs">
                {formatDuration(entry.duration_before)}
              </span>
            )}
            {entry.duration_before != null && entry.duration_after != null && (
              <span className="text-xs text-muted-foreground">&rarr;</span>
            )}
            {entry.duration_after != null && (
              <span className="text-xs font-medium">
                {formatDuration(entry.duration_after)}
              </span>
            )}
            {entry.params && Object.keys(entry.params).length > 0 && (
              <span className="text-xs text-muted-foreground ml-2">
                {Object.entries(entry.params)
                  .map(([k, v]) => `${k}: ${typeof v === "number" ? formatDuration(v) : v}`)
                  .join(", ")}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── Sessions Viewer ──────────────────────────────────────────────────────

function SessionsViewer({
  sessions,
}: {
  sessions: AudioEditReviewDetail["sessions"]
}) {
  if (sessions.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-muted-foreground">
        No saved sessions.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {sessions.map((session) => (
        <Card key={session.session_id}>
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    session.trigger === "submit"
                      ? "default"
                      : session.trigger === "manual"
                        ? "secondary"
                        : "outline"
                  }
                  className="text-xs"
                >
                  {session.trigger}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {session.edit_count} edit{session.edit_count !== 1 ? "s" : ""}
                </span>
              </div>
              <span className="text-xs text-muted-foreground">
                {new Date(session.updated_at).toLocaleString()}
              </span>
            </div>

            {/* Duration info */}
            {session.original_duration_seconds != null && (
              <div className="flex items-center gap-2 text-sm mb-2">
                <span className="text-muted-foreground">
                  {formatDuration(session.original_duration_seconds)}
                </span>
                <span className="text-muted-foreground">&rarr;</span>
                <span className="font-medium">
                  {formatDuration(session.audio_duration_seconds)}
                </span>
                {session.audio_duration_seconds != null && (
                  <span className="text-xs text-red-500">
                    {formatDurationChange(
                      session.original_duration_seconds,
                      session.audio_duration_seconds
                    )}
                  </span>
                )}
              </div>
            )}

            {/* Operations breakdown */}
            {session.summary?.operations_breakdown && (
              <div className="flex flex-wrap gap-1">
                {Object.entries(session.summary.operations_breakdown).map(
                  ([op, count]) => (
                    <Badge key={op} variant="secondary" className="text-xs">
                      {op}: {count}
                    </Badge>
                  )
                )}
              </div>
            )}

            <div className="text-xs text-muted-foreground mt-2 font-mono truncate">
              {session.session_id}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────

export default function AudioEditsPage() {
  const { showTestData } = useAdminSettings()

  // List state
  const [reviews, setReviews] = useState<AudioEditReviewSummary[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [offset, setOffset] = useState(0)
  const [searchQuery, setSearchQuery] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [loading, setLoading] = useState(true)

  // Detail dialog state
  const [detailDialogOpen, setDetailDialogOpen] = useState(false)
  const [rawDataDialogOpen, setRawDataDialogOpen] = useState(false)
  const [selectedDetail, setSelectedDetail] = useState<AudioEditReviewDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const fetchReviews = useCallback(async () => {
    setLoading(true)
    try {
      const data: AudioEditReviewListResponse =
        await adminApi.listAudioEditReviews({
          limit: PAGE_SIZE,
          offset,
          exclude_test: !showTestData,
          search: searchQuery || undefined,
        })
      setReviews(data.reviews)
      setTotal(data.total)
      setHasMore(data.has_more)
    } catch (err) {
      toast.error(
        "Failed to load audio edit reviews: " +
          (err instanceof Error ? err.message : String(err))
      )
    } finally {
      setLoading(false)
    }
  }, [offset, searchQuery, showTestData])

  useEffect(() => {
    fetchReviews()
  }, [fetchReviews])

  const loadDetail = useCallback(async (jobId: string) => {
    setDetailLoading(true)
    try {
      const detail = await adminApi.getAudioEditReview(jobId)
      setSelectedDetail(detail)
      return detail
    } catch (err) {
      toast.error(
        "Failed to load detail: " +
          (err instanceof Error ? err.message : String(err))
      )
      return null
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleViewDetail = useCallback(
    async (jobId: string) => {
      const detail = await loadDetail(jobId)
      if (detail) setDetailDialogOpen(true)
    },
    [loadDetail]
  )

  const handleViewRawData = useCallback(
    async (jobId: string) => {
      const detail = await loadDetail(jobId)
      if (detail) setRawDataDialogOpen(true)
    },
    [loadDetail]
  )

  const handleSearch = useCallback(() => {
    setOffset(0)
    setSearchQuery(searchInput)
  }, [searchInput])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") handleSearch()
    },
    [handleSearch]
  )

  const [copiedJson, setCopiedJson] = useState(false)
  const handleCopyJson = useCallback(() => {
    if (!selectedDetail) return
    navigator.clipboard.writeText(JSON.stringify(selectedDetail, null, 2))
    setCopiedJson(true)
    toast.success("Copied to clipboard")
    setTimeout(() => setCopiedJson(false), 2000)
  }, [selectedDetail])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Audio Edits</h1>
        <p className="text-muted-foreground">
          Review input audio editing sessions and edit history
        </p>
      </div>

      {/* Search */}
      <div className="flex gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by artist, title, or email..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={handleKeyDown}
            className="pl-8"
          />
        </div>
        <Button onClick={handleSearch} variant="secondary">
          Search
        </Button>
      </div>

      {/* Results */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base">
            {loading
              ? "Loading..."
              : `${total} job${total !== 1 ? "s" : ""} with audio edits`}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-4 space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : reviews.length === 0 ? (
            <div className="flex items-center justify-center h-48 text-muted-foreground">
              No audio edit reviews found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Artist / Title</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Edits</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Sessions</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reviews.map((review) => (
                  <TableRow key={review.job_id}>
                    <TableCell>
                      <div className="font-medium">{review.artist}</div>
                      <div className="text-sm text-muted-foreground">
                        {review.title}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          review.status === "complete"
                            ? "default"
                            : review.status.includes("audio_edit")
                              ? "secondary"
                              : "outline"
                        }
                      >
                        {review.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="font-medium">{review.total_edits}</span>
                      {review.latest_trigger && (
                        <Badge variant="outline" className="ml-1 text-xs">
                          {review.latest_trigger}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      {review.original_duration != null ? (
                        <div className="flex items-center gap-1">
                          <span className="text-muted-foreground">
                            {formatDuration(review.original_duration)}
                          </span>
                          {review.current_duration != null &&
                            review.current_duration !== review.original_duration && (
                              <>
                                <span className="text-muted-foreground">
                                  &rarr;
                                </span>
                                <span className="font-medium">
                                  {formatDuration(review.current_duration)}
                                </span>
                              </>
                            )}
                        </div>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      {review.session_count}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {review.updated_at
                        ? new Date(review.updated_at).toLocaleDateString()
                        : "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleViewDetail(review.job_id)}
                          disabled={detailLoading}
                          title="View Detail"
                        >
                          <Eye className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleViewRawData(review.job_id)}
                          disabled={detailLoading}
                          title="View Raw Data"
                        >
                          <Code className="w-4 h-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {/* Pagination */}
          {!loading && total > PAGE_SIZE && (
            <div className="flex items-center justify-between p-4 border-t">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() =>
                  setOffset((o) => Math.max(0, o - PAGE_SIZE))
                }
              >
                <ChevronLeft className="w-4 h-4 mr-1" /> Previous
              </Button>
              <span className="text-sm text-muted-foreground">
                Showing {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of{" "}
                {total}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={!hasMore}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Next <ChevronRight className="w-4 h-4 ml-1" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Detail Dialog */}
      <Dialog open={detailDialogOpen} onOpenChange={setDetailDialogOpen}>
        <DialogContent className="max-w-3xl w-full max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>
              {selectedDetail
                ? `${selectedDetail.job.artist} - ${selectedDetail.job.title}`
                : "Loading..."}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            {detailLoading ? (
              <div className="flex items-center justify-center h-48">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              </div>
            ) : selectedDetail ? (
              <Tabs defaultValue="edit-stack" className="w-full">
                <TabsList className="w-full">
                  <TabsTrigger value="edit-stack" className="flex-1">
                    <Scissors className="w-3 h-3 mr-1" />
                    Edit Stack ({selectedDetail.edit_stack.length})
                  </TabsTrigger>
                  <TabsTrigger value="sessions" className="flex-1">
                    <Clock className="w-3 h-3 mr-1" />
                    Sessions ({selectedDetail.sessions.length})
                  </TabsTrigger>
                  <TabsTrigger value="audio" className="flex-1">
                    <Play className="w-3 h-3 mr-1" />
                    Audio
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="edit-stack">
                  <ScrollArea className="h-[500px]">
                    <EditStackViewer editStack={selectedDetail.edit_stack} />
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="sessions">
                  <ScrollArea className="h-[500px]">
                    <SessionsViewer sessions={selectedDetail.sessions} />
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="audio">
                  <div className="space-y-4 p-2">
                    {selectedDetail.original_audio_url && (
                      <div>
                        <div className="text-sm font-medium mb-2">
                          Original Audio
                        </div>
                        <audio
                          controls
                          src={selectedDetail.original_audio_url}
                          className="w-full"
                        />
                      </div>
                    )}
                    {selectedDetail.current_audio_url && (
                      <div>
                        <div className="text-sm font-medium mb-2">
                          Current Audio
                          {selectedDetail.original_audio_url
                            ? " (after edits)"
                            : ""}
                        </div>
                        <audio
                          controls
                          src={selectedDetail.current_audio_url}
                          className="w-full"
                        />
                      </div>
                    )}
                    {!selectedDetail.original_audio_url &&
                      !selectedDetail.current_audio_url && (
                        <div className="flex items-center justify-center h-32 text-muted-foreground">
                          No audio URLs available.
                        </div>
                      )}
                  </div>
                </TabsContent>
              </Tabs>
            ) : (
              <div className="flex items-center justify-center h-48 text-muted-foreground">
                No data available.
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Raw Data Dialog */}
      <Dialog open={rawDataDialogOpen} onOpenChange={setRawDataDialogOpen}>
        <DialogContent className="max-w-4xl w-full max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>
              Raw Data
              {selectedDetail && (
                <span className="text-sm font-normal text-muted-foreground ml-2">
                  {selectedDetail.job.artist} - {selectedDetail.job.title}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            <div className="flex justify-end mb-2">
              <Button variant="outline" size="sm" onClick={handleCopyJson}>
                {copiedJson ? (
                  <Check className="w-3 h-3 mr-1" />
                ) : (
                  <Copy className="w-3 h-3 mr-1" />
                )}
                Copy
              </Button>
            </div>
            <ScrollArea className="h-[500px]">
              <pre className="text-xs bg-muted/50 p-3 rounded-md overflow-x-auto">
                {JSON.stringify(selectedDetail, null, 2) ?? "null"}
              </pre>
            </ScrollArea>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
