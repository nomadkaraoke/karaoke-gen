"use client"

import { useEffect, useState } from "react"
import { adminApi, AdminBetaStats, AdminBetaFeedback } from "@/lib/api"
import { StatsCard, StatsGrid } from "@/components/admin/stats-card"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Users,
  MessageSquare,
  Star,
  RefreshCw,
  Loader2,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/use-toast"

export default function AdminBetaPage() {
  const { toast } = useToast()
  const [stats, setStats] = useState<AdminBetaStats | null>(null)
  const [feedback, setFeedback] = useState<AdminBetaFeedback[]>([])
  const [loading, setLoading] = useState(true)

  const loadData = async () => {
    try {
      setLoading(true)
      const [statsData, feedbackData] = await Promise.all([
        adminApi.getBetaStats(),
        adminApi.getBetaFeedback(50),
      ])
      setStats(statsData)
      setFeedback(feedbackData.feedback || [])
    } catch (err: any) {
      console.error("Failed to load beta data:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load beta program data",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const renderStars = (rating?: number) => {
    if (!rating) return "—"
    return (
      <div className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((star) => (
          <Star
            key={star}
            className={`w-4 h-4 ${
              star <= rating ? "fill-yellow-400 text-yellow-400" : "text-muted"
            }`}
          />
        ))}
      </div>
    )
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "—"
    const date = new Date(dateStr)
    if (isNaN(date.getTime())) return "—"
    return date.toLocaleDateString()
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Beta Program</h1>
          <p className="text-muted-foreground">
            Manage beta testers and view feedback
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Stats Grid */}
      <StatsGrid>
        <StatsCard
          title="Total Beta Testers"
          value={stats?.total_beta_testers ?? 0}
          icon={Users}
          loading={loading}
        />
        <StatsCard
          title="Active Testers"
          value={stats?.active_testers ?? 0}
          icon={Users}
          loading={loading}
        />
        <StatsCard
          title="Pending Feedback"
          value={stats?.pending_feedback ?? 0}
          icon={MessageSquare}
          loading={loading}
        />
        <StatsCard
          title="Total Feedback"
          value={stats?.total_feedback_submissions ?? 0}
          icon={MessageSquare}
          loading={loading}
        />
      </StatsGrid>

      {/* Average Ratings */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Average Ratings</CardTitle>
          <CardDescription>Overall feedback scores from beta testers</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : stats?.average_ratings ? (
            <div className="grid gap-4 md:grid-cols-4">
              <div className="rounded-lg border p-4">
                <p className="text-sm text-muted-foreground">Overall</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-2xl font-bold">
                    {stats.average_ratings.overall?.toFixed(1) || "—"}
                  </span>
                  <span className="text-muted-foreground">/ 5</span>
                </div>
              </div>
              <div className="rounded-lg border p-4">
                <p className="text-sm text-muted-foreground">Ease of Use</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-2xl font-bold">
                    {stats.average_ratings.ease_of_use?.toFixed(1) || "—"}
                  </span>
                  <span className="text-muted-foreground">/ 5</span>
                </div>
              </div>
              <div className="rounded-lg border p-4">
                <p className="text-sm text-muted-foreground">Lyrics Accuracy</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-2xl font-bold">
                    {stats.average_ratings.lyrics_accuracy?.toFixed(1) || "—"}
                  </span>
                  <span className="text-muted-foreground">/ 5</span>
                </div>
              </div>
              <div className="rounded-lg border p-4">
                <p className="text-sm text-muted-foreground">Correction Experience</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-2xl font-bold">
                    {stats.average_ratings.correction_experience?.toFixed(1) || "—"}
                  </span>
                  <span className="text-muted-foreground">/ 5</span>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-muted-foreground">No ratings available yet</p>
          )}
        </CardContent>
      </Card>

      {/* Feedback List */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent Feedback</CardTitle>
          <CardDescription>Feedback submissions from beta testers</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : feedback.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">No feedback submissions yet</p>
          ) : (
            <div className="space-y-4">
              {feedback.map((item) => (
                <div key={item.id} className="border rounded-lg p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="font-medium text-sm">{item.user_email}</span>
                        {item.job_id && (
                          <Badge variant="outline" className="text-xs">
                            Job: {item.job_id}
                          </Badge>
                        )}
                        <span className="text-xs text-muted-foreground">
                          {formatDate(item.created_at)}
                        </span>
                      </div>

                      {/* Ratings */}
                      <div className="flex flex-wrap gap-4 mb-3 text-sm">
                        <div className="flex items-center gap-1">
                          <span className="text-muted-foreground">Overall:</span>
                          {renderStars(item.overall_rating)}
                        </div>
                        {item.ease_of_use_rating && (
                          <div className="flex items-center gap-1">
                            <span className="text-muted-foreground">Ease:</span>
                            {renderStars(item.ease_of_use_rating)}
                          </div>
                        )}
                        {item.lyrics_accuracy_rating && (
                          <div className="flex items-center gap-1">
                            <span className="text-muted-foreground">Accuracy:</span>
                            {renderStars(item.lyrics_accuracy_rating)}
                          </div>
                        )}
                      </div>

                      {/* Comments */}
                      {item.what_went_well && (
                        <div className="mb-2">
                          <p className="text-xs text-muted-foreground">What went well:</p>
                          <p className="text-sm">{item.what_went_well}</p>
                        </div>
                      )}
                      {item.what_could_improve && (
                        <div className="mb-2">
                          <p className="text-xs text-muted-foreground">What could improve:</p>
                          <p className="text-sm">{item.what_could_improve}</p>
                        </div>
                      )}
                      {item.additional_comments && (
                        <div>
                          <p className="text-xs text-muted-foreground">Additional comments:</p>
                          <p className="text-sm">{item.additional_comments}</p>
                        </div>
                      )}
                    </div>

                    {/* Recommend/Use Again */}
                    <div className="flex flex-col gap-1 text-sm">
                      {item.would_recommend !== undefined && (
                        <div className="flex items-center gap-1">
                          {item.would_recommend ? (
                            <ThumbsUp className="w-4 h-4 text-green-500" />
                          ) : (
                            <ThumbsDown className="w-4 h-4 text-red-500" />
                          )}
                          <span className="text-xs text-muted-foreground">Recommend</span>
                        </div>
                      )}
                      {item.would_use_again !== undefined && (
                        <div className="flex items-center gap-1">
                          {item.would_use_again ? (
                            <ThumbsUp className="w-4 h-4 text-green-500" />
                          ) : (
                            <ThumbsDown className="w-4 h-4 text-red-500" />
                          )}
                          <span className="text-xs text-muted-foreground">Use Again</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
