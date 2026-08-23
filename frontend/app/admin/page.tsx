"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
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
          href="/admin/users"
        />
        <StatsCard
          title="Total Jobs"
          value={stats?.total_jobs ?? 0}
          description={`${stats?.jobs_last_7d ?? 0} in last 7 days`}
          icon={Briefcase}
          loading={loading}
          href="/admin/jobs"
        />
        <StatsCard
          title="Revenue (30d)"
          value={revenue ? `$${(revenue.total_gross / 100).toFixed(2)}` : "$0.00"}
          description={revenue ? `${revenue.transaction_count} payments, ${`$${(revenue.total_net / 100).toFixed(2)}`} net` : "Loading..."}
          icon={DollarSign}
          loading={loading}
          valueClassName="text-green-600 dark:text-green-400"
          href="/admin/payments"
        />
        <StatsCard
          title="Credits Issued (30d)"
          value={stats?.total_credits_issued_30d ?? 0}
          description="Credits added to accounts"
          icon={CreditCard}
          loading={loading}
          href="/admin/payments"
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
              <div className="space-y-1">
                {[
                  { label: "Pending", icon: Clock, iconClass: "text-yellow-500", count: stats?.jobs_by_status?.pending ?? 0, href: "/admin/jobs?status=pending", badge: "secondary" as const },
                  { label: "Processing", icon: Loader2, iconClass: "text-blue-500", count: stats?.jobs_by_status?.processing ?? 0, href: "/admin/jobs", badge: "secondary" as const },
                  { label: "Awaiting Review", icon: AlertCircle, iconClass: "text-orange-500", count: stats?.jobs_by_status?.awaiting_review ?? 0, href: "/admin/jobs?status=awaiting_review", badge: "secondary" as const },
                  { label: "Awaiting Instrumental", icon: AlertCircle, iconClass: "text-purple-500", count: stats?.jobs_by_status?.awaiting_instrumental ?? 0, href: "/admin/jobs?status=awaiting_instrumental_selection", badge: "secondary" as const },
                  { label: "Complete", icon: CheckCircle, iconClass: "text-green-500", count: stats?.jobs_by_status?.complete ?? 0, href: "/admin/jobs?status=complete", badge: "secondary" as const },
                  { label: "Failed", icon: XCircle, iconClass: "text-red-500", count: stats?.jobs_by_status?.failed ?? 0, href: "/admin/jobs?status=failed", badge: "destructive" as const },
                  { label: "Cancelled", icon: XCircle, iconClass: "text-muted-foreground", count: stats?.jobs_by_status?.cancelled ?? 0, href: "/admin/jobs?status=cancelled", badge: "outline" as const },
                ].map(({ label, icon: RowIcon, iconClass, count, href, badge }) => (
                  <Link
                    key={label}
                    href={href}
                    className="flex items-center justify-between rounded-md px-2 py-1.5 -mx-2 hover:bg-muted/60 transition-colors"
                    title={`View ${label.toLowerCase()} jobs`}
                  >
                    <div className="flex items-center gap-2">
                      <RowIcon className={`w-4 h-4 ${iconClass}`} />
                      <span className="text-sm">{label}</span>
                    </div>
                    <Badge variant={badge}>{count}</Badge>
                  </Link>
                ))}
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
