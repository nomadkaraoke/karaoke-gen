"use client"

import { useEffect, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import NextLink from "next/link"
import { adminApi, AdminUserDetail, UserPaymentHistory } from "@/lib/api"
import type { ReferralLink } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  ArrowLeft,
  RefreshCw,
  Loader2,
  Plus,
  UserX,
  UserCheck,
  CreditCard,
  Briefcase,
  Clock,
  Trash2,
  DollarSign,
  LogIn,
  Tag,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"
import { useAuth } from "@/lib/auth"
import { IpInfo } from "@/components/admin/ip-info"

export default function AdminUserDetailPage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const { toast } = useToast()
  const { user: currentUser, startImpersonation } = useAuth()
  const email = searchParams.get("email") || ""
  const [impersonating, setImpersonating] = useState(false)

  const [user, setUser] = useState<AdminUserDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)
  const [paymentHistory, setPaymentHistory] = useState<UserPaymentHistory | null>(null)
  const [paymentsLoading, setPaymentsLoading] = useState(false)

  // Delete dialog
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  // Credit dialog
  const [creditDialogOpen, setCreditDialogOpen] = useState(false)
  const [creditAmount, setCreditAmount] = useState("")
  const [creditReason, setCreditReason] = useState("")

  // Discount dialog
  const [discountDialogOpen, setDiscountDialogOpen] = useState(false)
  const [discountCode, setDiscountCode] = useState("")
  const [discountDays, setDiscountDays] = useState("365")
  const [applyingDiscount, setApplyingDiscount] = useState(false)
  const [referralLinks, setReferralLinks] = useState<ReferralLink[]>([])
  const [referralLinksLoading, setReferralLinksLoading] = useState(false)

  const loadUser = async () => {
    if (!email) {
      setLoading(false)
      return
    }
    try {
      setLoading(true)
      const data = await adminApi.getUserDetail(email)
      setUser(data)
    } catch (err: any) {
      console.error("Failed to load user:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load user",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }

  const loadPayments = async () => {
    if (!email) return
    try {
      setPaymentsLoading(true)
      const data = await adminApi.getUserPayments(email)
      setPaymentHistory(data)
    } catch (err) {
      console.error("Failed to load payment history:", err)
    } finally {
      setPaymentsLoading(false)
    }
  }

  const loadReferralLinks = async () => {
    try {
      setReferralLinksLoading(true)
      const data = await adminApi.listReferralLinks({ limit: 200 })
      setReferralLinks(data.links.filter((l) => l.enabled))
    } catch (err) {
      console.error("Failed to load referral links:", err)
    } finally {
      setReferralLinksLoading(false)
    }
  }

  useEffect(() => {
    loadUser()
    loadPayments()
  }, [email])

  const handleAddCredits = async () => {
    if (!creditAmount || !creditReason) return

    const amount = parseInt(creditAmount.trim(), 10)
    if (!Number.isInteger(amount) || amount < 1 || amount > 100000) {
      toast({
        title: "Invalid Amount",
        description: "Please enter a valid amount between 1 and 100,000",
        variant: "destructive",
      })
      return
    }

    try {
      setActionLoading(true)
      await adminApi.addCredits(email, amount, creditReason)
      toast({
        title: "Credits Added",
        description: `Added ${creditAmount} credits`,
      })
      setCreditDialogOpen(false)
      setCreditAmount("")
      setCreditReason("")
      loadUser()
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to add credits",
        variant: "destructive",
      })
    } finally {
      setActionLoading(false)
    }
  }

  const handleApplyDiscount = async () => {
    const selectedLink = referralLinks.find((l) => l.code === discountCode)
    if (!selectedLink) {
      toast({ title: "Error", description: "Please select a referral code", variant: "destructive" })
      return
    }
    setApplyingDiscount(true)
    try {
      const result = await adminApi.applyDiscount(email, {
        discount_percent: selectedLink.discount_percent,
        duration_days: Number(discountDays) || 365,
        referral_code: discountCode,
      })
      toast({ title: "Discount Applied", description: result.message })
      setDiscountDialogOpen(false)
      loadUser()
    } catch (err) {
      toast({
        title: "Failed",
        description: err instanceof Error ? err.message : "Failed to apply discount",
        variant: "destructive",
      })
    } finally {
      setApplyingDiscount(false)
    }
  }

  const handleToggleStatus = async () => {
    if (!user) return

    try {
      setActionLoading(true)
      if (user.is_active) {
        await adminApi.disableUser(email)
        toast({
          title: "User Disabled",
          description: "User has been disabled",
        })
      } else {
        await adminApi.enableUser(email)
        toast({
          title: "User Enabled",
          description: "User has been enabled",
        })
      }
      loadUser()
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to update user status",
        variant: "destructive",
      })
    } finally {
      setActionLoading(false)
    }
  }

  const handleImpersonate = async () => {
    if (!email || email === currentUser?.email) return
    try {
      setImpersonating(true)
      const success = await startImpersonation(email)
      if (success) {
        router.push("/app")
      } else {
        const authError = useAuth.getState().error
        toast({
          title: "Impersonation Failed",
          description: authError || "Unable to impersonate user",
          variant: "destructive",
        })
      }
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to impersonate user",
        variant: "destructive",
      })
    } finally {
      setImpersonating(false)
    }
  }

  const handleDeleteUser = async () => {
    if (!user) return

    try {
      setActionLoading(true)
      await adminApi.deleteUser(email)
      toast({
        title: "User Deleted",
        description: `User ${email} has been permanently deleted`,
      })
      router.push("/admin/users")
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to delete user",
        variant: "destructive",
      })
    } finally {
      setActionLoading(false)
    }
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "Never"
    return new Date(dateStr).toLocaleString()
  }

  if (!email) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">No user email provided</p>
        <Button variant="outline" className="mt-4" onClick={() => router.push("/admin/users")}>
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Users
        </Button>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">User not found</p>
        <Button variant="outline" className="mt-4" onClick={() => router.push("/admin/users")}>
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Users
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => router.push("/admin/users")} title="Back to users list">
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              {user.display_name || user.email}
              <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                {user.role}
              </Badge>
              {!user.is_active && <Badge variant="destructive">Disabled</Badge>}
            </h1>
            <p className="text-muted-foreground">{user.email}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadUser} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {email !== currentUser?.email && (
          <Button
            variant="outline"
            onClick={handleImpersonate}
            disabled={impersonating || actionLoading}
            title="View the app as this user"
          >
            {impersonating ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <LogIn className="w-4 h-4 mr-2" />
            )}
            Impersonate User
          </Button>
        )}
        <Button onClick={() => setCreditDialogOpen(true)} disabled={actionLoading} title="Grant credits to this user">
          <Plus className="w-4 h-4 mr-2" />
          Add Credits
        </Button>
        <Button
          variant="outline"
          onClick={() => setDiscountDialogOpen(true)}
          disabled={actionLoading}
          title="Apply a referral discount to this user"
        >
          <Tag className="w-4 h-4 mr-2" />
          Apply Discount
        </Button>
        <Button
          variant={user.is_active ? "destructive" : "outline"}
          onClick={handleToggleStatus}
          disabled={actionLoading}
          title={user.is_active ? "Disable this user account" : "Enable this user account"}
        >
          {user.is_active ? (
            <>
              <UserX className="w-4 h-4 mr-2" />
              Disable User
            </>
          ) : (
            <>
              <UserCheck className="w-4 h-4 mr-2" />
              Enable User
            </>
          )}
        </Button>
        {user.role !== "admin" && (
          <Button
            variant="destructive"
            onClick={() => setDeleteDialogOpen(true)}
            disabled={actionLoading}
            title="Permanently delete this user"
          >
            <Trash2 className="w-4 h-4 mr-2" />
            Delete User
          </Button>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <CreditCard className="w-4 h-4" />
              Credits
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{user.credits}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Briefcase className="w-4 h-4" />
              Jobs Created
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{user.total_jobs_created}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Briefcase className="w-4 h-4" />
              Jobs Completed
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{user.total_jobs_completed}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="w-4 h-4" />
              Active Sessions
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{user.active_sessions_count}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Account Info */}
        <Card>
          <CardHeader>
            <CardTitle>Account Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Email Verified</span>
              <Badge variant={user.email_verified ? "default" : "secondary"}>
                {user.email_verified ? "Yes" : "No"}
              </Badge>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Created</span>
              <span className="text-sm">{formatDate(user.created_at)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Last Login</span>
              <span className="text-sm">{formatDate(user.last_login_at)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Last Updated</span>
              <span className="text-sm">{formatDate(user.updated_at)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total Spent</span>
              <span className="text-sm font-medium">{user.total_spent ? `$${(user.total_spent / 100).toFixed(2)}` : "$0"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Feedback Submitted</span>
              <Badge variant={user.has_submitted_feedback ? "default" : "secondary"}>
                {user.has_submitted_feedback ? "Yes" : "No"}
              </Badge>
            </div>
            {user.referral_discount_expires_at && (
              <div className="flex items-center gap-2 text-sm">
                <Tag className="w-4 h-4 text-purple-500" />
                <span>
                  Referral discount active
                  {user.referred_by_code && <> (code: <code className="bg-muted px-1 rounded">{user.referred_by_code}</code>)</>}
                  {' · '}Expires {new Date(user.referral_discount_expires_at).toLocaleDateString()}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Identity & Security */}
        <Card>
          <CardHeader>
            <CardTitle>Identity & Security</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-muted-foreground">Signup IP</span>
              <IpInfo ip={user.signup_ip} />
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Device Fingerprint</span>
              <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{user.device_fingerprint || "unknown"}</code>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Welcome Credits</span>
              <Badge variant={user.welcome_credits_granted ? "default" : "secondary"}>
                {user.welcome_credits_granted ? "Granted" : "Not yet"}
              </Badge>
            </div>
            {user.recent_sessions && user.recent_sessions.length > 0 && (
              <div className="pt-2 border-t">
                <p className="text-sm font-medium mb-2">Active Sessions</p>
                <div className="space-y-2">
                  {user.recent_sessions.map((s, i) => (
                    <div key={i} className="text-xs bg-muted rounded p-2 space-y-1">
                      <div className="flex items-center gap-1"><span className="text-muted-foreground">IP:</span> <IpInfo ip={s.ip_address} compact /></div>
                      {s.device_fingerprint && <div><span className="text-muted-foreground">Fingerprint:</span> {s.device_fingerprint}</div>}
                      {s.user_agent && <div className="truncate"><span className="text-muted-foreground">UA:</span> {s.user_agent}</div>}
                      <div><span className="text-muted-foreground">Last active:</span> {s.last_activity_at ? new Date(s.last_activity_at).toLocaleString() : "unknown"}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Credit Transactions */}
        <Card>
          <CardHeader>
            <CardTitle>Recent Credit Transactions</CardTitle>
            <CardDescription>Last 20 transactions</CardDescription>
          </CardHeader>
          <CardContent>
            {user.credit_transactions.length === 0 ? (
              <p className="text-muted-foreground text-sm">No transactions</p>
            ) : (
              <div className="space-y-2 max-h-[300px] overflow-y-auto">
                {user.credit_transactions.slice().reverse().map((txn, i) => (
                  <div key={txn.id || i} className="flex justify-between items-center text-sm border-b pb-2">
                    <div>
                      <span className={txn.amount > 0 ? "text-green-500" : "text-red-500"}>
                        {txn.amount > 0 ? "+" : ""}{txn.amount}
                      </span>
                      <span className="text-muted-foreground ml-2">{txn.reason}</span>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {new Date(txn.created_at).toLocaleDateString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Payment History */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <DollarSign className="w-5 h-5" />
                Payment History
              </CardTitle>
              <CardDescription>
                {paymentHistory ? (
                  <>
                    {paymentHistory.payment_count} payment{paymentHistory.payment_count !== 1 ? "s" : ""} - Lifetime spend: ${(paymentHistory.total_spent / 100).toFixed(2)}
                    {paymentHistory.total_refunded > 0 && (
                      <> (${(paymentHistory.total_refunded / 100).toFixed(2)} refunded)</>
                    )}
                  </>
                ) : paymentsLoading ? (
                  "Loading..."
                ) : (
                  "No payment data"
                )}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {paymentsLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : !paymentHistory || paymentHistory.payments.length === 0 ? (
            <p className="text-muted-foreground text-sm">No payments found</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Amount</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paymentHistory.payments.map((p) => (
                  <TableRow
                    key={p.session_id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => router.push(`/admin/payments?tab=transactions&email=${encodeURIComponent(p.customer_email)}`)}
                  >
                    <TableCell className="text-sm">
                      {p.created_at ? new Date(p.created_at).toLocaleDateString() : "-"}
                    </TableCell>
                    <TableCell className="text-sm font-medium">
                      ${(p.amount_total / 100).toFixed(2)}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {p.order_type === "made_for_you" ? "MFY" : "Credits"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm max-w-[160px] truncate">
                      {p.product_description}
                    </TableCell>
                    <TableCell>
                      <Badge variant={
                        p.status === "succeeded" ? "default" :
                        p.status === "refunded" ? "destructive" :
                        "secondary"
                      }>
                        {p.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Recent Jobs */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Jobs</CardTitle>
          <CardDescription>Last 10 jobs by this user</CardDescription>
        </CardHeader>
        <CardContent>
          {user.recent_jobs.length === 0 ? (
            <p className="text-muted-foreground text-sm">No jobs</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job ID</TableHead>
                  <TableHead>Artist / Title</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {user.recent_jobs.map((job) => (
                  <TableRow
                    key={job.job_id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => router.push(`/admin/jobs?id=${job.job_id}`)}
                    title="View job details"
                  >
                    <TableCell className="font-mono text-sm">{job.job_id}</TableCell>
                    <TableCell>
                      {job.artist && job.title ? `${job.artist} - ${job.title}` : "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={
                        job.status === "complete" ? "default" :
                        job.status === "failed" ? "destructive" :
                        "secondary"
                      }>
                        {job.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm">
                      {job.created_at ? new Date(job.created_at).toLocaleString() : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Add Credits Dialog */}
      <Dialog open={creditDialogOpen} onOpenChange={setCreditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Credits</DialogTitle>
            <DialogDescription>
              Add credits to {user.email}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="amount">Amount</Label>
              <Input
                id="amount"
                type="number"
                min="1"
                placeholder="Enter amount"
                value={creditAmount}
                onChange={(e) => setCreditAmount(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reason">Reason</Label>
              <Input
                id="reason"
                placeholder="e.g., Beta reward, Support credit"
                value={creditReason}
                onChange={(e) => setCreditReason(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreditDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleAddCredits}
              disabled={!creditAmount || !creditReason || actionLoading}
            >
              {actionLoading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Add Credits
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Apply Discount Dialog */}
      <Dialog open={discountDialogOpen} onOpenChange={(open) => {
        setDiscountDialogOpen(open)
        if (open && referralLinks.length === 0) loadReferralLinks()
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply Discount</DialogTitle>
            <DialogDescription>
              Link {email} to an existing referral code. They&apos;ll get the code&apos;s discount on credit purchases.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Referral Code</Label>
              {referralLinksLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading codes…
                </div>
              ) : (
                <Select value={discountCode} onValueChange={(code) => {
                  setDiscountCode(code)
                  const link = referralLinks.find((l) => l.code === code)
                  if (link) setDiscountDays(String(link.discount_duration_days))
                }}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a referral code…" />
                  </SelectTrigger>
                  <SelectContent>
                    {referralLinks.map((link) => (
                      <SelectItem key={link.code} value={link.code}>
                        {link.code} — {link.discount_percent}% off
                        {link.display_name ? ` (${link.display_name})` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {referralLinks.length > 0 && !referralLinksLoading && referralLinks.every((l) => l.code !== discountCode) && !discountCode && (
                <p className="text-xs text-muted-foreground">
                  Create codes in <NextLink href="/admin/referrals" className="underline">Referral Management</NextLink> first.
                </p>
              )}
            </div>
            {discountCode && (() => {
              const sel = referralLinks.find((l) => l.code === discountCode)
              return sel ? (
                <div className="rounded-md bg-muted p-3 text-sm space-y-1">
                  <p><strong>Discount:</strong> {sel.discount_percent}% off credit purchases</p>
                  <p><strong>Default duration:</strong> {sel.discount_duration_days} days (override below)</p>
                </div>
              ) : null
            })()}
            <div className="space-y-2">
              <Label>Duration (days)</Label>
              <Input
                type="number"
                min="1"
                value={discountDays}
                onChange={(e) => setDiscountDays(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">How long the discount stays active. Use 3650 for ~10 years.</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscountDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleApplyDiscount} disabled={applyingDiscount || !discountCode}>
              {applyingDiscount && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Apply Discount
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete User Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete User</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to permanently delete user {email}? This action
              cannot be undone. All sessions and authentication data will be removed.
              Jobs will be preserved as historical records.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteUser}
              disabled={actionLoading}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {actionLoading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Delete User
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
