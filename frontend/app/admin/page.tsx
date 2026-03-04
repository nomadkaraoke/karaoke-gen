"use client"

import { useEffect, useState } from "react"
import { adminApi, AdminStatsOverview, RevenueSummary } from "@/lib/api"
import { useAdminSettings } from "@/lib/admin-settings"
import { StatsCard, StatsGrid } from "@/components/admin/stats-card"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Users,
  Briefcase,
  CreditCard,
  DollarSign,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  Loader2,
  RefreshCw,
} from "lucide-react"
import { Button } from "@/components/ui/button"

export default function AdminDashboardPage() {
  const [stats, setStats] = useState<AdminStatsOverview | null>(null)
  const [revenue, setRevenue] = useState<RevenueSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { showTestData } = useAdminSettings()

  const loadStats = async () => {
    try {
      setLoading(true)
      setError(null)
      const [data, revenueData] = await Promise.all([
        adminApi.getStats({ exclude_test: !showTestData }),
        adminApi.getPaymentSummary({ days: 30, exclude_test: !showTestData }).catch(() => null),
      ])
      setStats(data)
      setRevenue(revenueData)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load statistics"
      setError(message)
      console.error("Failed to load admin stats:", err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStats()
  }, [showTestData])

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <XCircle className="w-12 h-12 text-destructive mb-4" />
        <h2 className="text-lg font-semibold mb-2">Failed to load statistics</h2>
        <p className="text-muted-foreground mb-4">{error}</p>
        <Button onClick={loadStats}>
          <RefreshCw className="w-4 h-4 mr-2" />
          Retry
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground">
            Overview of your karaoke platform
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadStats} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Main Stats Grid */}
      <StatsGrid>
        <StatsCard
          title="Total Users"
          value={stats?.total_users ?? 0}
          description={`${stats?.active_users_7d ?? 0} active in last 7 days`}
          icon={Users}
          loading={loading}
        />
        <StatsCard
          title="Total Jobs"
          value={stats?.total_jobs ?? 0}
          description={`${stats?.jobs_last_7d ?? 0} in last 7 days`}
          icon={Briefcase}
          loading={loading}
        />
        <StatsCard
          title="Revenue (30d)"
          value={revenue ? `$${(revenue.total_gross / 100).toFixed(2)}` : "$0.00"}
          description={revenue ? `${revenue.transaction_count} payments, ${`$${(revenue.total_net / 100).toFixed(2)}`} net` : "Loading..."}
          icon={DollarSign}
          loading={loading}
          valueClassName="text-green-600 dark:text-green-400"
        />
        <StatsCard
          title="Credits Issued (30d)"
          value={stats?.total_credits_issued_30d ?? 0}
          description="Credits added to accounts"
          icon={CreditCard}
          loading={loading}
        />
      </StatsGrid>

      {/* Secondary Stats */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Job Status Breakdown */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Job Status Breakdown</CardTitle>
            <CardDescription>Current status of all jobs in the system</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4 text-yellow-500" />
                    <span className="text-sm">Pending</span>
                  </div>
                  <Badge variant="secondary">{stats?.jobs_by_status?.pending ?? 0}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 text-blue-500" />
                    <span className="text-sm">Processing</span>
                  </div>
                  <Badge variant="secondary">{stats?.jobs_by_status?.processing ?? 0}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-orange-500" />
                    <span className="text-sm">Awaiting Review</span>
                  </div>
                  <Badge variant="secondary">{stats?.jobs_by_status?.awaiting_review ?? 0}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-purple-500" />
                    <span className="text-sm">Awaiting Instrumental</span>
                  </div>
                  <Badge variant="secondary">{stats?.jobs_by_status?.awaiting_instrumental ?? 0}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-green-500" />
                    <span className="text-sm">Complete</span>
                  </div>
                  <Badge variant="secondary">{stats?.jobs_by_status?.complete ?? 0}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <XCircle className="w-4 h-4 text-red-500" />
                    <span className="text-sm">Failed</span>
                  </div>
                  <Badge variant="destructive">{stats?.jobs_by_status?.failed ?? 0}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <XCircle className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm">Cancelled</span>
                  </div>
                  <Badge variant="outline">{stats?.jobs_by_status?.cancelled ?? 0}</Badge>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Activity Summary */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Activity Summary</CardTitle>
            <CardDescription>User and job activity over time</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <h4 className="text-sm font-medium mb-2">Users</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="rounded-lg border p-3">
                      <p className="text-2xl font-bold">{stats?.active_users_7d ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Active (7d)</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-2xl font-bold">{stats?.active_users_30d ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Active (30d)</p>
                    </div>
                  </div>
                </div>
                <div>
                  <h4 className="text-sm font-medium mb-2">Jobs</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="rounded-lg border p-3">
                      <p className="text-2xl font-bold">{stats?.jobs_last_7d ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Created (7d)</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-2xl font-bold">{stats?.jobs_last_30d ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Created (30d)</p>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
