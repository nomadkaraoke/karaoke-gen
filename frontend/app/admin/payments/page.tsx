"use client"

import { useEffect, useState, useCallback } from "react"
import {
  adminApi,
  RevenueSummary,
  RevenueChartPoint,
  PaymentRecord,
  PaymentListResponse,
  StripeBalance,
  PayoutRecord,
  DisputeRecord,
  WebhookEvent,
} from "@/lib/api"
import { useAdminSettings } from "@/lib/admin-settings"
import { StatsCard, StatsGrid } from "@/components/admin/stats-card"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DollarSign,
  TrendingUp,
  CreditCard,
  Receipt,
  RefreshCw,
  Loader2,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  ExternalLink,
  Search,
  Banknote,
  Webhook,
  ShieldAlert,
} from "lucide-react"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer, PieChart, Pie, Cell } from "recharts"
import { PaymentDetailSheet } from "@/components/admin/payment-detail"
import { RefundDialog } from "@/components/admin/refund-dialog"

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return "-"
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

function formatDateTime(dateStr?: string | null): string {
  if (!dateStr) return "-"
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function formatTimestamp(ts?: number | null): string {
  if (!ts) return "-"
  return new Date(ts * 1000).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

function StatusBadge({ status }: { status: string }) {
  const variants: Record<string, { variant: "default" | "secondary" | "destructive" | "outline"; label: string }> = {
    succeeded: { variant: "default", label: "Succeeded" },
    refunded: { variant: "destructive", label: "Refunded" },
    partially_refunded: { variant: "secondary", label: "Partial Refund" },
    disputed: { variant: "destructive", label: "Disputed" },
    processing: { variant: "secondary", label: "Processing" },
    success: { variant: "default", label: "Success" },
    error: { variant: "destructive", label: "Error" },
    skipped: { variant: "outline", label: "Skipped" },
    paid: { variant: "default", label: "Paid" },
    pending: { variant: "secondary", label: "Pending" },
    in_transit: { variant: "secondary", label: "In Transit" },
    canceled: { variant: "outline", label: "Canceled" },
    failed: { variant: "destructive", label: "Failed" },
    // Dispute statuses
    needs_response: { variant: "destructive", label: "Needs Response" },
    under_review: { variant: "secondary", label: "Under Review" },
    won: { variant: "default", label: "Won" },
    lost: { variant: "destructive", label: "Lost" },
    warning_needs_response: { variant: "destructive", label: "Needs Response" },
    warning_under_review: { variant: "secondary", label: "Under Review" },
    warning_closed: { variant: "outline", label: "Closed" },
  }
  const v = variants[status] || { variant: "outline" as const, label: status }
  return <Badge variant={v.variant}>{v.label}</Badge>
}

function PaymentMethodDisplay({ type, brand, last4 }: { type: string; brand: string; last4: string }) {
  if (!type || type === "unknown") return <span className="text-muted-foreground">-</span>
  if (type === "card" && brand && last4) {
    return (
      <span className="text-sm">
        <span className="capitalize">{brand}</span> ...{last4}
      </span>
    )
  }
  return <span className="text-sm capitalize">{type.replace("_", " ")}</span>
}

// =============================================================================
// Revenue Overview Tab
// =============================================================================

function RevenueOverviewTab() {
  const { showTestData } = useAdminSettings()
  const [summary, setSummary] = useState<RevenueSummary | null>(null)
  const [chart, setChart] = useState<RevenueChartPoint[]>([])
  const [balance, setBalance] = useState<StripeBalance | null>(null)
  const [disputes, setDisputes] = useState<DisputeRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [period, setPeriod] = useState(30)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [summaryData, chartData, balanceData, disputeData] = await Promise.all([
        adminApi.getPaymentSummary({ days: period, exclude_test: !showTestData }),
        adminApi.getRevenueChart({ days: period, exclude_test: !showTestData }),
        adminApi.getStripeBalance(),
        adminApi.getDisputes(),
      ])
      setSummary(summaryData)
      setChart(chartData)
      setBalance(balanceData)
      setDisputes(disputeData)
    } catch (err) {
      console.error("Failed to load revenue data:", err)
    } finally {
      setLoading(false)
    }
  }, [period, showTestData])

  useEffect(() => { loadData() }, [loadData])

  const openDisputes = disputes.filter(d => ["needs_response", "warning_needs_response", "under_review", "warning_under_review"].includes(d.status))

  const chartConfig = {
    gross: { label: "Gross Revenue", color: "hsl(var(--chart-1))" },
    net: { label: "Net Revenue", color: "hsl(var(--chart-2))" },
  }

  const pieData = summary?.revenue_by_type
    ? Object.entries(summary.revenue_by_type).map(([type, amount]) => ({
        name: type === "credit_purchase" ? "Credits" : type === "made_for_you" ? "Made For You" : type,
        value: amount,
      }))
    : []
  const PIE_COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))"]

  return (
    <div className="space-y-6">
      {/* Dispute Alert Banner */}
      {openDisputes.length > 0 && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-destructive" />
            <span className="font-medium text-destructive">
              {openDisputes.length} open dispute{openDisputes.length > 1 ? "s" : ""} requiring attention
            </span>
          </div>
          <div className="mt-2 space-y-1">
            {openDisputes.map(d => (
              <div key={d.id} className="text-sm text-muted-foreground">
                {formatCents(d.amount)} - {d.reason} - Evidence due {formatTimestamp(d.evidence_due_by)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Period selector */}
      <div className="flex items-center gap-2">
        <Select value={String(period)} onValueChange={(v) => setPeriod(Number(v))}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">Last 7 days</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
            <SelectItem value="90">Last 90 days</SelectItem>
            <SelectItem value="0">All time</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Stats Grid */}
      <StatsGrid>
        <StatsCard
          title="Total Revenue"
          value={summary ? formatCents(summary.total_gross) : "$0.00"}
          description={`${summary?.transaction_count ?? 0} transactions`}
          icon={DollarSign}
          loading={loading}
          valueClassName="text-green-600 dark:text-green-400"
        />
        <StatsCard
          title="Stripe Fees"
          value={summary ? formatCents(summary.total_fees) : "$0.00"}
          description={summary && summary.total_gross > 0 ? `${((summary.total_fees / summary.total_gross) * 100).toFixed(1)}% of gross` : ""}
          icon={Receipt}
          loading={loading}
        />
        <StatsCard
          title="Net Revenue"
          value={summary ? formatCents(summary.total_net) : "$0.00"}
          description="After fees"
          icon={TrendingUp}
          loading={loading}
          valueClassName="text-green-600 dark:text-green-400"
        />
        <StatsCard
          title="Avg Order Value"
          value={summary ? formatCents(summary.average_order_value) : "$0.00"}
          description="Per transaction"
          icon={CreditCard}
          loading={loading}
        />
      </StatsGrid>

      {/* Charts */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Revenue Chart */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Revenue Over Time</CardTitle>
            <CardDescription>Daily gross and net revenue</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className="h-[250px] w-full" />
            ) : chart.length > 0 ? (
              <ChartContainer config={chartConfig} className="h-[250px] w-full">
                <AreaChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(v) => new Date(v).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                    fontSize={12}
                  />
                  <YAxis
                    tickFormatter={(v) => `$${(v / 100).toFixed(0)}`}
                    fontSize={12}
                  />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        formatter={(value) => formatCents(value as number)}
                      />
                    }
                  />
                  <Area
                    type="monotone"
                    dataKey="gross"
                    stroke="var(--color-gross)"
                    fill="var(--color-gross)"
                    fillOpacity={0.2}
                    name="Gross"
                  />
                  <Area
                    type="monotone"
                    dataKey="net"
                    stroke="var(--color-net)"
                    fill="var(--color-net)"
                    fillOpacity={0.2}
                    name="Net"
                  />
                </AreaChart>
              </ChartContainer>
            ) : (
              <div className="flex items-center justify-center h-[250px] text-muted-foreground">
                No revenue data for this period
              </div>
            )}
          </CardContent>
        </Card>

        {/* Revenue by Type + Balance */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">By Product</CardTitle>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className="h-[100px]" />
              ) : pieData.length > 0 ? (
                <div className="space-y-2">
                  {pieData.map((item, i) => (
                    <div key={item.name} className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div
                          className="w-3 h-3 rounded-full"
                          style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }}
                        />
                        <span className="text-sm">{item.name}</span>
                      </div>
                      <span className="text-sm font-medium">{formatCents(item.value)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">No data</div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Stripe Balance</CardTitle>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className="h-[60px]" />
              ) : balance ? (
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <span className="text-sm text-muted-foreground">Available</span>
                    <span className="text-sm font-medium text-green-600 dark:text-green-400">
                      {formatCents(balance.available)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-muted-foreground">Pending</span>
                    <span className="text-sm font-medium">{formatCents(balance.pending)}</span>
                  </div>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">Unable to load</div>
              )}
            </CardContent>
          </Card>

          {summary && summary.total_refunds > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Refunds</CardTitle>
              </CardHeader>
              <CardContent>
                <span className="text-lg font-bold text-destructive">{formatCents(summary.total_refunds)}</span>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

// =============================================================================
// Transactions Tab
// =============================================================================

function TransactionsTab() {
  const { showTestData } = useAdminSettings()
  const [data, setData] = useState<PaymentListResponse>({ payments: [], total: 0, has_more: false })
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  const [filterType, setFilterType] = useState<string>("all")
  const [filterStatus, setFilterStatus] = useState<string>("all")
  const [emailSearch, setEmailSearch] = useState("")
  const [selectedPayment, setSelectedPayment] = useState<PaymentRecord | null>(null)
  const [refundPayment, setRefundPayment] = useState<PaymentRecord | null>(null)
  const limit = 50

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const result = await adminApi.listPayments({
        limit,
        offset,
        order_type: filterType === "all" ? undefined : filterType,
        status: filterStatus === "all" ? undefined : filterStatus,
        email: emailSearch || undefined,
        exclude_test: !showTestData,
      })
      setData(result)
    } catch (err) {
      console.error("Failed to load payments:", err)
    } finally {
      setLoading(false)
    }
  }, [offset, filterType, filterStatus, emailSearch, showTestData])

  useEffect(() => { loadData() }, [loadData])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setOffset(0)
    loadData()
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Select value={filterType} onValueChange={(v) => { setFilterType(v); setOffset(0) }}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Order Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="credit_purchase">Credit Purchase</SelectItem>
            <SelectItem value="made_for_you">Made For You</SelectItem>
          </SelectContent>
        </Select>

        <Select value={filterStatus} onValueChange={(v) => { setFilterStatus(v); setOffset(0) }}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Statuses</SelectItem>
            <SelectItem value="succeeded">Succeeded</SelectItem>
            <SelectItem value="refunded">Refunded</SelectItem>
            <SelectItem value="partially_refunded">Partial Refund</SelectItem>
            <SelectItem value="disputed">Disputed</SelectItem>
          </SelectContent>
        </Select>

        <form onSubmit={handleSearch} className="flex gap-2">
          <Input
            placeholder="Search by email..."
            value={emailSearch}
            onChange={(e) => setEmailSearch(e.target.value)}
            className="w-[200px]"
          />
          <Button type="submit" variant="outline" size="icon">
            <Search className="h-4 w-4" />
          </Button>
        </form>

        <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>

        <span className="text-sm text-muted-foreground ml-auto">
          {data.total} payment{data.total !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead className="text-right">Fee</TableHead>
              <TableHead className="text-right">Net</TableHead>
              <TableHead>Method</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 9 }).map((_, j) => (
                    <TableCell key={j}><Skeleton className="h-4 w-16" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : data.payments.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="text-center py-8 text-muted-foreground">
                  No payments found
                </TableCell>
              </TableRow>
            ) : (
              data.payments.map((payment) => (
                <TableRow
                  key={payment.session_id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setSelectedPayment(payment)}
                >
                  <TableCell className="text-sm">{formatDate(payment.created_at)}</TableCell>
                  <TableCell className="text-sm max-w-[160px] truncate">{payment.customer_email}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-xs">
                      {payment.order_type === "made_for_you" ? "MFY" : "Credits"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm max-w-[140px] truncate">{payment.product_description}</TableCell>
                  <TableCell className="text-right text-sm font-medium">{formatCents(payment.amount_total)}</TableCell>
                  <TableCell className="text-right text-sm text-muted-foreground">{formatCents(payment.stripe_fee)}</TableCell>
                  <TableCell className="text-right text-sm">{formatCents(payment.net_amount)}</TableCell>
                  <TableCell>
                    <PaymentMethodDisplay
                      type={payment.payment_method_type}
                      brand={payment.card_brand}
                      last4={payment.card_last4}
                    />
                  </TableCell>
                  <TableCell><StatusBadge status={payment.status} /></TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {data.total > limit && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            Showing {offset + 1}-{Math.min(offset + limit, data.total)} of {data.total}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOffset(offset + limit)}
              disabled={!data.has_more}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Payment Detail Sheet */}
      <PaymentDetailSheet
        payment={selectedPayment}
        onClose={() => setSelectedPayment(null)}
        onRefund={(payment) => {
          setSelectedPayment(null)
          setRefundPayment(payment)
        }}
      />

      {/* Refund Dialog */}
      <RefundDialog
        payment={refundPayment}
        onClose={() => setRefundPayment(null)}
        onSuccess={() => {
          setRefundPayment(null)
          loadData()
        }}
      />
    </div>
  )
}

// =============================================================================
// Payouts Tab
// =============================================================================

function PayoutsTab() {
  const [balance, setBalance] = useState<StripeBalance | null>(null)
  const [payouts, setPayouts] = useState<PayoutRecord[]>([])
  const [loading, setLoading] = useState(true)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [balanceData, payoutData] = await Promise.all([
        adminApi.getStripeBalance(),
        adminApi.getPayouts(20),
      ])
      setBalance(balanceData)
      setPayouts(payoutData)
    } catch (err) {
      console.error("Failed to load payouts:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  return (
    <div className="space-y-6">
      {/* Balance */}
      <StatsGrid columns={3}>
        <StatsCard
          title="Available Balance"
          value={balance ? formatCents(balance.available) : "$0.00"}
          description="Ready for payout"
          icon={Banknote}
          loading={loading}
          valueClassName="text-green-600 dark:text-green-400"
        />
        <StatsCard
          title="Pending Balance"
          value={balance ? formatCents(balance.pending) : "$0.00"}
          description="Processing"
          icon={Clock}
          loading={loading}
        />
        <StatsCard
          title="Recent Payouts"
          value={payouts.length}
          description="Shown below"
          icon={DollarSign}
          loading={loading}
        />
      </StatsGrid>

      {/* Payouts Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Payout History</CardTitle>
          <CardDescription>When money hits the bank account</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Arrival</TableHead>
                <TableHead>Method</TableHead>
                <TableHead>ID</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 6 }).map((_, j) => (
                      <TableCell key={j}><Skeleton className="h-4 w-16" /></TableCell>
                    ))}
                  </TableRow>
                ))
              ) : payouts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-8 text-muted-foreground">
                    No payouts found
                  </TableCell>
                </TableRow>
              ) : (
                payouts.map((payout) => (
                  <TableRow key={payout.id}>
                    <TableCell className="text-sm">{formatTimestamp(payout.created)}</TableCell>
                    <TableCell className="text-right text-sm font-medium">{formatCents(payout.amount)}</TableCell>
                    <TableCell><StatusBadge status={payout.status} /></TableCell>
                    <TableCell className="text-sm">{formatTimestamp(payout.arrival_date)}</TableCell>
                    <TableCell className="text-sm capitalize">{payout.method || "-"}</TableCell>
                    <TableCell className="text-xs text-muted-foreground font-mono">{payout.id.slice(-8)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

// =============================================================================
// Webhook Events Tab
// =============================================================================

function WebhookEventsTab() {
  const [events, setEvents] = useState<WebhookEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [filterType, setFilterType] = useState<string>("all")
  const [filterStatus, setFilterStatus] = useState<string>("all")

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const data = await adminApi.getWebhookEvents({
        limit: 100,
        event_type: filterType === "all" ? undefined : filterType,
        status: filterStatus === "all" ? undefined : filterStatus,
      })
      setEvents(data)
    } catch (err) {
      console.error("Failed to load webhook events:", err)
    } finally {
      setLoading(false)
    }
  }, [filterType, filterStatus])

  useEffect(() => { loadData() }, [loadData])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Select value={filterType} onValueChange={setFilterType}>
          <SelectTrigger className="w-[240px]">
            <SelectValue placeholder="Event Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Events</SelectItem>
            <SelectItem value="checkout.session.completed">checkout.session.completed</SelectItem>
            <SelectItem value="charge.refunded">charge.refunded</SelectItem>
            <SelectItem value="charge.dispute.created">charge.dispute.created</SelectItem>
            <SelectItem value="payment_intent.payment_failed">payment_intent.payment_failed</SelectItem>
            <SelectItem value="checkout.session.expired">checkout.session.expired</SelectItem>
          </SelectContent>
        </Select>

        <Select value={filterStatus} onValueChange={setFilterStatus}>
          <SelectTrigger className="w-[140px]">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="success">Success</SelectItem>
            <SelectItem value="error">Error</SelectItem>
            <SelectItem value="skipped">Skipped</SelectItem>
          </SelectContent>
        </Select>

        <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Event Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Summary</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 5 }).map((_, j) => (
                    <TableCell key={j}><Skeleton className="h-4 w-20" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : events.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                  No webhook events found
                </TableCell>
              </TableRow>
            ) : (
              events.map((event) => (
                <TableRow key={event.event_id}>
                  <TableCell className="text-sm">{formatDateTime(event.created_at)}</TableCell>
                  <TableCell className="text-xs font-mono">{event.event_type}</TableCell>
                  <TableCell><StatusBadge status={event.status} /></TableCell>
                  <TableCell className="text-sm max-w-[160px] truncate">{event.customer_email || "-"}</TableCell>
                  <TableCell className="text-sm max-w-[240px] truncate">{event.summary || "-"}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

// =============================================================================
// Main Page
// =============================================================================

export default function PaymentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Payments</h1>
        <p className="text-muted-foreground">Revenue analytics, transactions, and Stripe management</p>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Revenue</TabsTrigger>
          <TabsTrigger value="transactions">Transactions</TabsTrigger>
          <TabsTrigger value="payouts">Payouts</TabsTrigger>
          <TabsTrigger value="events">Webhook Events</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <RevenueOverviewTab />
        </TabsContent>

        <TabsContent value="transactions">
          <TransactionsTab />
        </TabsContent>

        <TabsContent value="payouts">
          <PayoutsTab />
        </TabsContent>

        <TabsContent value="events">
          <WebhookEventsTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
