"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { adminApi, AdminUser, AdminUserListResponse } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import {
  Search,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Plus,
  Loader2,
  CreditCard,
  Shield,
  ShieldOff,
  UserX,
  UserCheck,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"

export default function AdminUsersPage() {
  const router = useRouter()
  const { toast } = useToast()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [limit] = useState(20)
  const [hasMore, setHasMore] = useState(false)
  const [search, setSearch] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [sortBy, setSortBy] = useState<"created_at" | "last_login_at" | "credits" | "email">("created_at")
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc")

  // Credit dialog state
  const [creditDialogOpen, setCreditDialogOpen] = useState(false)
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null)
  const [creditAmount, setCreditAmount] = useState("")
  const [creditReason, setCreditReason] = useState("")
  const [addingCredits, setAddingCredits] = useState(false)

  const loadUsers = useCallback(async () => {
    try {
      setLoading(true)
      const data = await adminApi.listUsers({
        limit,
        offset,
        search: search || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      })
      setUsers(data.users)
      setTotal(data.total)
      setHasMore(data.has_more)
    } catch (err: any) {
      console.error("Failed to load users:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load users",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }, [limit, offset, search, sortBy, sortOrder, toast])

  useEffect(() => {
    loadUsers()
  }, [loadUsers])

  const handleSearch = () => {
    setOffset(0)
    setSearch(searchInput)
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch()
    }
  }

  const handleAddCredits = async () => {
    if (!selectedUser || !creditAmount || !creditReason) return

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
      setAddingCredits(true)
      await adminApi.addCredits(selectedUser.email, amount, creditReason)
      toast({
        title: "Credits Added",
        description: `Added ${creditAmount} credits to ${selectedUser.email}`,
      })
      setCreditDialogOpen(false)
      setCreditAmount("")
      setCreditReason("")
      setSelectedUser(null)
      loadUsers()
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to add credits",
        variant: "destructive",
      })
    } finally {
      setAddingCredits(false)
    }
  }

  const handleToggleRole = async (user: AdminUser) => {
    const newRole = user.role === "admin" ? "user" : "admin"
    try {
      await adminApi.setUserRole(user.email, newRole)
      toast({
        title: "Role Updated",
        description: `${user.email} is now ${newRole}`,
      })
      loadUsers()
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to update role",
        variant: "destructive",
      })
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Users</h1>
          <p className="text-muted-foreground">
            Manage user accounts and credits
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadUsers} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        <div className="flex-1 flex gap-2">
          <Input
            placeholder="Search by email..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyPress={handleKeyPress}
            className="max-w-sm"
          />
          <Button variant="secondary" onClick={handleSearch}>
            <Search className="w-4 h-4" />
          </Button>
        </div>
        <div className="flex gap-2">
          <Select value={sortBy} onValueChange={(v) => setSortBy(v as any)}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="Sort by" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="created_at">Created</SelectItem>
              <SelectItem value="last_login_at">Last Login</SelectItem>
              <SelectItem value="credits">Credits</SelectItem>
              <SelectItem value="email">Email</SelectItem>
            </SelectContent>
          </Select>
          <Select value={sortOrder} onValueChange={(v) => setSortOrder(v as any)}>
            <SelectTrigger className="w-[100px]">
              <SelectValue placeholder="Order" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="desc">Desc</SelectItem>
              <SelectItem value="asc">Asc</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead className="text-right">Credits</TableHead>
              <TableHead className="text-right">Jobs</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin mx-auto text-muted-foreground" />
                </TableCell>
              </TableRow>
            ) : users.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                  No users found
                </TableCell>
              </TableRow>
            ) : (
              users.map((user) => (
                <TableRow
                  key={user.email}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => router.push(`/admin/users/${encodeURIComponent(user.email)}`)}
                >
                  <TableCell className="font-medium">
                    {user.display_name || user.email}
                    {user.display_name && (
                      <span className="text-muted-foreground text-sm block">
                        {user.email}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                      {user.role}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">{user.credits}</TableCell>
                  <TableCell className="text-right">
                    {user.total_jobs_created ?? 0} / {user.total_jobs_completed ?? 0}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          setSelectedUser(user)
                          setCreditDialogOpen(true)
                        }}
                        title="Add credits"
                      >
                        <Plus className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleToggleRole(user)}
                        title={user.role === "admin" ? "Remove admin" : "Make admin"}
                      >
                        {user.role === "admin" ? (
                          <ShieldOff className="w-4 h-4" />
                        ) : (
                          <Shield className="w-4 h-4" />
                        )}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {total === 0 ? "No users found" : `Showing ${offset + 1}-${Math.min(offset + users.length, total)} of ${total} users`}
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOffset(Math.max(0, offset - limit))}
            disabled={offset === 0}
          >
            <ChevronLeft className="w-4 h-4" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOffset(offset + limit)}
            disabled={!hasMore}
          >
            Next
            <ChevronRight className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Add Credits Dialog */}
      <Dialog open={creditDialogOpen} onOpenChange={setCreditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Credits</DialogTitle>
            <DialogDescription>
              Add credits to {selectedUser?.email}
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
              disabled={!creditAmount || !creditReason || addingCredits}
            >
              {addingCredits && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Add Credits
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
