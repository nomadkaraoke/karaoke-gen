"use client"

import { useEffect, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { adminApi, AdminUserDetail } from "@/lib/api"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"

export default function AdminUserDetailPage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const { toast } = useToast()
  const email = searchParams.get("email") || ""

  const [user, setUser] = useState<AdminUserDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)

  // Credit dialog
  const [creditDialogOpen, setCreditDialogOpen] = useState(false)
  const [creditAmount, setCreditAmount] = useState("")
  const [creditReason, setCreditReason] = useState("")

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

  useEffect(() => {
    loadUser()
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
          <Button variant="ghost" size="icon" onClick={() => router.push("/admin/users")}>
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
        <Button onClick={() => setCreditDialogOpen(true)} disabled={actionLoading}>
          <Plus className="w-4 h-4 mr-2" />
          Add Credits
        </Button>
        <Button
          variant={user.is_active ? "destructive" : "outline"}
          onClick={handleToggleStatus}
          disabled={actionLoading}
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
              <span className="text-muted-foreground">Beta Tester</span>
              <Badge variant={user.is_beta_tester ? "default" : "secondary"}>
                {user.is_beta_tester ? user.beta_tester_status || "Yes" : "No"}
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
                    onClick={() => router.push(`/admin/jobs/detail?id=${job.job_id}`)}
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
    </div>
  )
}
