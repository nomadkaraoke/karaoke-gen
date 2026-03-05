"use client"

import { useEffect, useState, useCallback } from "react"
import {
  adminApi,
  EditReviewSummary,
  EditReviewListResponse,
  EditReviewDetail,
  AdminEditLogEntry,
} from "@/lib/api"
import { CorrectionData } from "@/lib/lyrics-review/types"
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
  FileText,
  Code,
  ChevronLeft,
  ChevronRight,
  Copy,
  Check,
  MessageSquare,
  ArrowRight,
} from "lucide-react"
import { toast } from "sonner"
import dynamic from "next/dynamic"

const LyricsAnalyzer = dynamic(
  () => import("@/components/lyrics-review/LyricsAnalyzer"),
  { ssr: false }
)

const PAGE_SIZE = 20

// ─── Edit Log Replay Panel ───────────────────────────────────────────────────

function EditLogReplayPanel({
  entries,
}: {
  entries: AdminEditLogEntry[]
}) {
  const [currentIndex, setCurrentIndex] = useState(0)

  if (entries.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-muted-foreground">
        No edit log entries found.
      </div>
    )
  }

  const operationCounts: Record<string, number> = {}
  let feedbackCount = 0
  for (const entry of entries) {
    operationCounts[entry.operation] = (operationCounts[entry.operation] || 0) + 1
    if (entry.feedback) feedbackCount++
  }

  const current = entries[currentIndex]

  return (
    <div className="space-y-4">
      {/* Summary Stats */}
      <div className="grid grid-cols-3 gap-3">
        <Card>
          <CardHeader className="py-2 px-3">
            <CardTitle className="text-xs font-medium text-muted-foreground">Total Edits</CardTitle>
          </CardHeader>
          <CardContent className="py-1 px-3">
            <div className="text-2xl font-bold">{entries.length}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="py-2 px-3">
            <CardTitle className="text-xs font-medium text-muted-foreground">With Feedback</CardTitle>
          </CardHeader>
          <CardContent className="py-1 px-3">
            <div className="text-2xl font-bold">{feedbackCount}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="py-2 px-3">
            <CardTitle className="text-xs font-medium text-muted-foreground">Operation Types</CardTitle>
          </CardHeader>
          <CardContent className="py-1 px-3">
            <div className="flex flex-wrap gap-1">
              {Object.entries(operationCounts).map(([op, count]) => (
                <Badge key={op} variant="secondary" className="text-xs">
                  {op}: {count}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between border rounded-md p-2">
        <Button
          variant="outline"
          size="sm"
          disabled={currentIndex === 0}
          onClick={() => setCurrentIndex((i) => i - 1)}
        >
          <ChevronLeft className="w-4 h-4 mr-1" /> Previous
        </Button>
        <span className="text-sm text-muted-foreground">
          {currentIndex + 1} / {entries.length}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={currentIndex === entries.length - 1}
          onClick={() => setCurrentIndex((i) => i + 1)}
        >
          Next <ChevronRight className="w-4 h-4 ml-1" />
        </Button>
      </div>

      {/* Entry List */}
      <ScrollArea className="h-[400px]">
        <div className="space-y-2">
          {entries.map((entry, idx) => {
            const isActive = idx === currentIndex
            return (
              <div
                key={entry.id}
                className={`border rounded-md p-3 cursor-pointer transition-colors ${
                  isActive ? "border-primary bg-primary/5" : "hover:bg-muted/50"
                }`}
                onClick={() => setCurrentIndex(idx)}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant={isActive ? "default" : "outline"} className="text-xs">
                    {entry.operation}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {new Date(entry.timestamp).toLocaleString()}
                  </span>
                  {entry.segment_index !== undefined && (
                    <span className="text-xs text-muted-foreground">
                      Segment {entry.segment_index}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2 text-sm">
                  {entry.text_before && (
                    <span className="line-through text-red-500/80 bg-red-50 dark:bg-red-950/30 px-1 rounded text-xs">
                      {entry.text_before}
                    </span>
                  )}
                  {entry.text_before && entry.text_after && (
                    <ArrowRight className="w-3 h-3 text-muted-foreground shrink-0" />
                  )}
                  {entry.text_after && (
                    <span className="text-green-600 bg-green-50 dark:bg-green-950/30 px-1 rounded text-xs">
                      {entry.text_after}
                    </span>
                  )}
                  {!entry.text_before && !entry.text_after && (
                    <span className="text-xs text-muted-foreground italic">
                      (no text change)
                    </span>
                  )}
                </div>

                {entry.feedback && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 rounded p-1.5">
                    <MessageSquare className="w-3 h-3 mt-0.5 shrink-0" />
                    <span>{entry.feedback.reason}</span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </ScrollArea>
    </div>
  )
}

// ─── Raw Data Viewer ─────────────────────────────────────────────────────────

function RawDataViewer({
  editLog,
  annotations,
  originalCorrections,
  updatedCorrections,
}: {
  editLog: unknown
  annotations: unknown
  originalCorrections: Record<string, unknown> | null
  updatedCorrections: Record<string, unknown> | null
}) {
  const [copiedTab, setCopiedTab] = useState<string | null>(null)

  const handleCopy = useCallback((data: unknown, tabName: string) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2))
    setCopiedTab(tabName)
    toast.success("Copied to clipboard")
    setTimeout(() => setCopiedTab(null), 2000)
  }, [])

  // Compute corrections diff
  const correctionsDiff = (() => {
    if (!originalCorrections || !updatedCorrections) return null
    const allKeys = new Set([
      ...Object.keys(originalCorrections),
      ...Object.keys(updatedCorrections),
    ])
    const diff: Record<string, { original: unknown; updated: unknown }> = {}
    for (const key of allKeys) {
      const origVal = JSON.stringify(originalCorrections[key])
      const updVal = JSON.stringify(updatedCorrections[key])
      if (origVal !== updVal) {
        diff[key] = {
          original: originalCorrections[key],
          updated: updatedCorrections[key],
        }
      }
    }
    return diff
  })()

  return (
    <Tabs defaultValue="edit-log" className="w-full">
      <TabsList className="w-full">
        <TabsTrigger value="edit-log" className="flex-1">Edit Log</TabsTrigger>
        <TabsTrigger value="annotations" className="flex-1">Annotations</TabsTrigger>
        <TabsTrigger value="corrections-diff" className="flex-1">Corrections Diff</TabsTrigger>
      </TabsList>

      <TabsContent value="edit-log">
        <div className="flex justify-end mb-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleCopy(editLog, "edit-log")}
          >
            {copiedTab === "edit-log" ? (
              <Check className="w-3 h-3 mr-1" />
            ) : (
              <Copy className="w-3 h-3 mr-1" />
            )}
            Copy
          </Button>
        </div>
        <ScrollArea className="h-[500px]">
          <pre className="text-xs bg-muted/50 p-3 rounded-md overflow-x-auto">
            {JSON.stringify(editLog, null, 2) ?? "null"}
          </pre>
        </ScrollArea>
      </TabsContent>

      <TabsContent value="annotations">
        <div className="flex justify-end mb-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleCopy(annotations, "annotations")}
          >
            {copiedTab === "annotations" ? (
              <Check className="w-3 h-3 mr-1" />
            ) : (
              <Copy className="w-3 h-3 mr-1" />
            )}
            Copy
          </Button>
        </div>
        <ScrollArea className="h-[500px]">
          <pre className="text-xs bg-muted/50 p-3 rounded-md overflow-x-auto">
            {JSON.stringify(annotations, null, 2) ?? "null"}
          </pre>
        </ScrollArea>
      </TabsContent>

      <TabsContent value="corrections-diff">
        <div className="flex justify-end mb-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleCopy(correctionsDiff, "corrections-diff")}
          >
            {copiedTab === "corrections-diff" ? (
              <Check className="w-3 h-3 mr-1" />
            ) : (
              <Copy className="w-3 h-3 mr-1" />
            )}
            Copy
          </Button>
        </div>
        <ScrollArea className="h-[500px]">
          {correctionsDiff && Object.keys(correctionsDiff).length > 0 ? (
            <div className="space-y-4">
              {Object.entries(correctionsDiff).map(([key, { original, updated }]) => (
                <div key={key} className="border rounded-md p-3">
                  <div className="font-mono text-sm font-semibold mb-2">{key}</div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <div className="text-xs font-medium text-muted-foreground mb-1">Original</div>
                      <pre className="text-xs bg-red-50 dark:bg-red-950/30 p-2 rounded overflow-x-auto">
                        {JSON.stringify(original, null, 2) ?? "undefined"}
                      </pre>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-muted-foreground mb-1">Updated</div>
                      <pre className="text-xs bg-green-50 dark:bg-green-950/30 p-2 rounded overflow-x-auto">
                        {JSON.stringify(updated, null, 2) ?? "undefined"}
                      </pre>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center justify-center h-48 text-muted-foreground">
              {!originalCorrections || !updatedCorrections
                ? "Original or updated corrections not available."
                : "No differences found between original and updated corrections."}
            </div>
          )}
        </ScrollArea>
      </TabsContent>
    </Tabs>
  )
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function EditReviewsPage() {
  const { showTestData } = useAdminSettings()

  // List state
  const [reviews, setReviews] = useState<EditReviewSummary[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [offset, setOffset] = useState(0)
  const [searchQuery, setSearchQuery] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [loading, setLoading] = useState(true)

  // Dialog state
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const [editLogDialogOpen, setEditLogDialogOpen] = useState(false)
  const [rawDataDialogOpen, setRawDataDialogOpen] = useState(false)
  const [selectedReview, setSelectedReview] = useState<EditReviewDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const fetchReviews = useCallback(async () => {
    setLoading(true)
    try {
      const data: EditReviewListResponse = await adminApi.listEditReviews({
        limit: PAGE_SIZE,
        offset,
        exclude_test: !showTestData,
        search: searchQuery || undefined,
      })
      setReviews(data.reviews)
      setTotal(data.total)
      setHasMore(data.has_more)
    } catch (err) {
      toast.error("Failed to load edit reviews: " + (err instanceof Error ? err.message : String(err)))
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
      const detail = await adminApi.getEditReview(jobId)
      setSelectedReview(detail)
      return detail
    } catch (err) {
      toast.error("Failed to load review detail: " + (err instanceof Error ? err.message : String(err)))
      return null
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleViewReview = useCallback(async (jobId: string) => {
    const detail = await loadDetail(jobId)
    if (detail) {
      setReviewDialogOpen(true)
    }
  }, [loadDetail])

  const handleViewEditLog = useCallback(async (jobId: string) => {
    const detail = await loadDetail(jobId)
    if (detail) {
      setEditLogDialogOpen(true)
    }
  }, [loadDetail])

  const handleViewRawData = useCallback(async (jobId: string) => {
    const detail = await loadDetail(jobId)
    if (detail) {
      setRawDataDialogOpen(true)
    }
  }, [loadDetail])

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

  const correctionData: CorrectionData | null =
    selectedReview?.updated_corrections
      ? (selectedReview.updated_corrections as unknown as CorrectionData)
      : selectedReview?.original_corrections
        ? (selectedReview.original_corrections as unknown as CorrectionData)
        : null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Edit Reviews</h1>
          <p className="text-muted-foreground">
            Review user lyrics edits and corrections
          </p>
        </div>
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
            {loading ? "Loading..." : `${total} review${total !== 1 ? "s" : ""} found`}
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
              No edit reviews found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Artist / Title</TableHead>
                  <TableHead>User Email</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reviews.map((review) => (
                  <TableRow key={review.job_id}>
                    <TableCell>
                      <div className="font-medium">{review.artist}</div>
                      <div className="text-sm text-muted-foreground">{review.title}</div>
                    </TableCell>
                    <TableCell className="text-sm">{review.user_email}</TableCell>
                    <TableCell>
                      <Badge variant={review.status === "completed" ? "default" : "secondary"}>
                        {review.status}
                      </Badge>
                      {review.has_corrections_updated && (
                        <Badge variant="outline" className="ml-1 text-xs">
                          edited
                        </Badge>
                      )}
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
                          onClick={() => handleViewReview(review.job_id)}
                          disabled={detailLoading}
                          title="View Review"
                        >
                          <Eye className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleViewEditLog(review.job_id)}
                          disabled={detailLoading}
                          title="View Edit Log"
                        >
                          <FileText className="w-4 h-4" />
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
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              >
                <ChevronLeft className="w-4 h-4 mr-1" /> Previous
              </Button>
              <span className="text-sm text-muted-foreground">
                Showing {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of {total}
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

      {/* Review Viewer Dialog */}
      <Dialog open={reviewDialogOpen} onOpenChange={setReviewDialogOpen}>
        <DialogContent className="max-w-[95vw] w-[95vw] h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>
              {selectedReview
                ? `${selectedReview.job.artist} - ${selectedReview.job.title}`
                : "Loading..."}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            {detailLoading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              </div>
            ) : correctionData ? (
              <LyricsAnalyzer
                data={correctionData}
                isReadOnly={true}
                apiClient={null}
                audioHash={selectedReview?.edit_log?.audio_hash ?? ""}
                onFileLoad={() => {}}
                onShowMetadata={() => {}}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground">
                No correction data available for this review.
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit Log Dialog */}
      <Dialog open={editLogDialogOpen} onOpenChange={setEditLogDialogOpen}>
        <DialogContent className="max-w-2xl w-full max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>
              Edit Log
              {selectedReview && (
                <span className="text-sm font-normal text-muted-foreground ml-2">
                  {selectedReview.job.artist} - {selectedReview.job.title}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            {detailLoading ? (
              <div className="flex items-center justify-center h-48">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              </div>
            ) : selectedReview?.edit_log?.entries ? (
              <EditLogReplayPanel entries={selectedReview.edit_log.entries} />
            ) : (
              <div className="flex items-center justify-center h-48 text-muted-foreground">
                No edit log available for this review.
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
              {selectedReview && (
                <span className="text-sm font-normal text-muted-foreground ml-2">
                  {selectedReview.job.artist} - {selectedReview.job.title}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            {detailLoading ? (
              <div className="flex items-center justify-center h-48">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              </div>
            ) : selectedReview ? (
              <RawDataViewer
                editLog={selectedReview.edit_log}
                annotations={selectedReview.annotations}
                originalCorrections={selectedReview.original_corrections}
                updatedCorrections={selectedReview.updated_corrections}
              />
            ) : (
              <div className="flex items-center justify-center h-48 text-muted-foreground">
                No data available.
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
