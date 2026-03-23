"use client"

import { Fragment, useEffect, useState, useCallback } from "react"
import { adminApi, AdminFeedbackItem, AdminFeedbackListResponse } from "@/lib/api"
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
  Search,
  Loader2,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Star,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react"
import { toast } from "sonner"

const PAGE_SIZE = 50

function StarRating({ rating }: { rating: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" title={`${rating}/5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          className={`w-3.5 h-3.5 ${
            i <= rating
              ? "fill-yellow-400 text-yellow-400"
              : "text-muted-foreground/30"
          }`}
        />
      ))}
    </span>
  )
}

function formatDate(dateStr: string | null) {
  if (!dateStr) return "—"
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  } catch {
    return dateStr
  }
}

function ExpandableText({ label, text }: { label: string; text: string | null }) {
  if (!text) return null
  return (
    <div className="mb-2">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <p className="text-sm whitespace-pre-wrap">{text}</p>
    </div>
  )
}

export default function AdminFeedbackPage() {
  const { showTestData } = useAdminSettings()
  const [data, setData] = useState<AdminFeedbackListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [offset, setOffset] = useState(0)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const fetchFeedback = useCallback(async () => {
    setLoading(true)
    try {
      const result = await adminApi.listFeedback({
        limit: PAGE_SIZE,
        offset,
        search: search || undefined,
        exclude_test: !showTestData,
      })
      setData(result)
    } catch (err) {
      toast.error("Failed to load feedback")
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [offset, search, showTestData])

  useEffect(() => {
    fetchFeedback()
  }, [fetchFeedback])

  const handleSearch = () => {
    setOffset(0)
    setSearch(searchInput)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch()
  }

  const toggleExpand = (id: string) => {
    setExpandedId(expandedId === id ? null : id)
  }

  const hasTextContent = (item: AdminFeedbackItem) =>
    item.what_went_well || item.what_could_improve || item.additional_comments

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">User Feedback</h1>
        <Button variant="outline" size="sm" onClick={fetchFeedback} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Aggregate stats */}
      {data && data.total > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Total Responses
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{data.total}</p>
            </CardContent>
          </Card>
          {[
            { label: "Overall", value: data.avg_overall_rating },
            { label: "Ease of Use", value: data.avg_ease_of_use_rating },
            { label: "Lyrics Accuracy", value: data.avg_lyrics_accuracy_rating },
            { label: "Correction Exp.", value: data.avg_correction_experience_rating },
          ].map(({ label, value }) => (
            <Card key={label}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Avg {label}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-2">
                  <p className="text-2xl font-bold">
                    {value != null ? value.toFixed(1) : "—"}
                  </p>
                  <span className="text-muted-foreground text-sm">/ 5</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Search */}
      <div className="flex gap-2">
        <Input
          placeholder="Search by email..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={handleKeyDown}
          className="max-w-sm"
        />
        <Button variant="outline" onClick={handleSearch}>
          <Search className="w-4 h-4 mr-2" />
          Search
        </Button>
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              No feedback submissions found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8"></TableHead>
                  <TableHead>User</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Overall</TableHead>
                  <TableHead>Ease of Use</TableHead>
                  <TableHead>Lyrics</TableHead>
                  <TableHead>Correction</TableHead>
                  <TableHead>Recommend</TableHead>
                  <TableHead>Use Again</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((item) => (
                  <Fragment key={item.id}>
                    <TableRow
                      className={`cursor-pointer hover:bg-muted/50 ${
                        expandedId === item.id ? "bg-muted/30" : ""
                      }`}
                      onClick={() => toggleExpand(item.id)}
                    >
                      <TableCell className="w-8">
                        {hasTextContent(item) && (
                          expandedId === item.id
                            ? <ChevronUp className="w-4 h-4 text-muted-foreground" />
                            : <ChevronDown className="w-4 h-4 text-muted-foreground" />
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {item.user_email}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground whitespace-nowrap">
                        {formatDate(item.created_at)}
                      </TableCell>
                      <TableCell>
                        <StarRating rating={item.overall_rating} />
                      </TableCell>
                      <TableCell>
                        <StarRating rating={item.ease_of_use_rating} />
                      </TableCell>
                      <TableCell>
                        <StarRating rating={item.lyrics_accuracy_rating} />
                      </TableCell>
                      <TableCell>
                        <StarRating rating={item.correction_experience_rating} />
                      </TableCell>
                      <TableCell>
                        {item.would_recommend ? (
                          <Badge variant="secondary" className="bg-green-500/10 text-green-600">
                            <ThumbsUp className="w-3 h-3 mr-1" />
                            Yes
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="bg-red-500/10 text-red-600">
                            <ThumbsDown className="w-3 h-3 mr-1" />
                            No
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        {item.would_use_again ? (
                          <Badge variant="secondary" className="bg-green-500/10 text-green-600">
                            <ThumbsUp className="w-3 h-3 mr-1" />
                            Yes
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="bg-red-500/10 text-red-600">
                            <ThumbsDown className="w-3 h-3 mr-1" />
                            No
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                    {expandedId === item.id && hasTextContent(item) && (
                      <TableRow>
                        <TableCell colSpan={9} className="bg-muted/20 px-8 py-4">
                          <ExpandableText label="What went well" text={item.what_went_well} />
                          <ExpandableText label="What could improve" text={item.what_could_improve} />
                          <ExpandableText label="Additional comments" text={item.additional_comments} />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              <ChevronLeft className="w-4 h-4 mr-1" />
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!data.has_more}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
              <ChevronRight className="w-4 h-4 ml-1" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
