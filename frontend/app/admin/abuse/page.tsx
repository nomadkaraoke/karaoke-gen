"use client"

import { useEffect, useState } from "react"
import { adminApi } from "@/lib/api"
import type {
  AbuseSuspiciousUser,
  AbuseRelatedResponse,
  AbuseRelatedUser,
} from "@/lib/api"
import { useToast } from "@/hooks/use-toast"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import {
  Loader2,
  Search,
  Fingerprint,
  Globe,
  AlertTriangle,
  ChevronRight,
  ArrowLeft,
  Users,
  ExternalLink,
} from "lucide-react"

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "-"
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
  } catch {
    return dateStr
  }
}

function formatCents(cents: number): string {
  if (cents === 0) return "$0"
  return `$${(cents / 100).toFixed(2)}`
}

function truncate(str: string | null | undefined, len: number): string {
  if (!str) return "-"
  return str.length > len ? str.slice(0, len) + "..." : str
}

// =============================================================================
// Suspicious Accounts Table
// =============================================================================

function SuspiciousAccountsTab({
  onInvestigate,
}: {
  onInvestigate: (email: string) => void
}) {
  const { toast } = useToast()
  const [users, setUsers] = useState<AbuseSuspiciousUser[]>([])
  const [loading, setLoading] = useState(true)
  const [minJobs, setMinJobs] = useState(2)
  const [maxSpend, setMaxSpend] = useState(0)

  const loadData = async () => {
    try {
      setLoading(true)
      const result = await adminApi.getAbuseSuspicious({
        min_jobs: minJobs,
        max_spend: maxSpend,
      })
      setUsers(result.users)
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-amber-500" />
          Suspicious Accounts
        </CardTitle>
        <CardDescription>
          Accounts with many jobs but no spend — potential free-credit abusers
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-4 mb-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground whitespace-nowrap">Min jobs:</label>
            <Input
              type="number"
              value={minJobs}
              onChange={(e) => setMinJobs(Number(e.target.value))}
              className="w-20"
              min={1}
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground whitespace-nowrap">Max spend ($):</label>
            <Input
              type="number"
              value={maxSpend / 100}
              onChange={(e) => setMaxSpend(Math.round(Number(e.target.value) * 100))}
              className="w-24"
              min={0}
              step={1}
            />
          </div>
          <Button onClick={loadData} disabled={loading} size="sm">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            <span className="ml-1">Search</span>
          </Button>
          <span className="text-sm text-muted-foreground ml-auto">
            {users.length} account{users.length !== 1 ? "s" : ""} found
          </span>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : users.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            No suspicious accounts found with these filters.
          </div>
        ) : (
          <div className="border rounded-lg overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-right">Jobs</TableHead>
                  <TableHead className="text-right">Credits</TableHead>
                  <TableHead className="text-right">Spent</TableHead>
                  <TableHead>Feedback</TableHead>
                  <TableHead>Signup IP</TableHead>
                  <TableHead>Fingerprint</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow key={user.email}>
                    <TableCell className="font-mono text-xs max-w-[200px] truncate">
                      {user.email}
                    </TableCell>
                    <TableCell className="text-right font-medium">
                      {user.total_jobs_created}
                      {user.total_jobs_completed > 0 && (
                        <span className="text-muted-foreground text-xs ml-1">
                          ({user.total_jobs_completed} done)
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">{user.credits}</TableCell>
                    <TableCell className="text-right">
                      {user.total_spent === 0 ? (
                        <Badge variant="destructive" className="text-xs">$0</Badge>
                      ) : (
                        formatCents(user.total_spent)
                      )}
                    </TableCell>
                    <TableCell>
                      {user.has_submitted_feedback ? (
                        <Badge variant="outline" className="text-xs">Yes</Badge>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {truncate(user.signup_ip, 15)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {truncate(user.device_fingerprint, 10)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(user.created_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onInvestigate(user.email)}
                        title="Investigate related accounts"
                      >
                        <ChevronRight className="w-4 h-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// =============================================================================
// Related Accounts Detail View
// =============================================================================

function RelatedAccountsView({
  email,
  onBack,
  onInvestigate,
}: {
  email: string
  onBack: () => void
  onInvestigate: (email: string) => void
}) {
  const { toast } = useToast()
  const [data, setData] = useState<AbuseRelatedResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true)
        const result = await adminApi.getAbuseRelated(email)
        setData(result)
      } catch (err: any) {
        toast({ title: "Error", description: err.message, variant: "destructive" })
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [email])

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!data) return null

  const { user, related_by_ip, related_by_fingerprint } = data
  const totalRelated = new Set([
    ...related_by_ip.map((u) => u.email),
    ...related_by_fingerprint.map((u) => u.email),
  ]).size

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back
        </Button>
        <h2 className="text-lg font-semibold">Investigating: {email}</h2>
        {totalRelated > 0 && (
          <Badge variant="destructive">{totalRelated} related account{totalRelated !== 1 ? "s" : ""}</Badge>
        )}
      </div>

      {/* Target user info */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Account Details</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-muted-foreground">Credits:</span>{" "}
              <span className="font-medium">{user.credits}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Jobs:</span>{" "}
              <span className="font-medium">{user.total_jobs_created}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Spent:</span>{" "}
              <span className="font-medium">{formatCents(user.total_spent)}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Created:</span>{" "}
              <span className="font-medium">{formatDate(user.created_at)}</span>
            </div>
            <div className="col-span-2">
              <span className="text-muted-foreground">Signup IP:</span>{" "}
              <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{user.signup_ip || "unknown"}</code>
            </div>
            <div className="col-span-2">
              <span className="text-muted-foreground">Fingerprint:</span>{" "}
              <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{user.device_fingerprint || "unknown"}</code>
            </div>
          </div>
          <div className="mt-3">
            <Button variant="outline" size="sm" asChild>
              <a href={`/admin/users/${encodeURIComponent(email)}`}>
                <ExternalLink className="w-3 h-3 mr-1" />
                View full user profile
              </a>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Related by IP */}
      <RelatedUsersCard
        title="Same Signup IP"
        icon={<Globe className="w-4 h-4 text-blue-500" />}
        identifier={user.signup_ip}
        identifierLabel="IP"
        users={related_by_ip}
        onInvestigate={onInvestigate}
        emptyMessage="No other accounts from this IP address"
      />

      {/* Related by fingerprint */}
      <RelatedUsersCard
        title="Same Device Fingerprint"
        icon={<Fingerprint className="w-4 h-4 text-purple-500" />}
        identifier={user.device_fingerprint}
        identifierLabel="Fingerprint"
        users={related_by_fingerprint}
        onInvestigate={onInvestigate}
        emptyMessage="No other accounts with this fingerprint"
      />
    </div>
  )
}

function RelatedUsersCard({
  title,
  icon,
  identifier,
  identifierLabel,
  users,
  onInvestigate,
  emptyMessage,
}: {
  title: string
  icon: React.ReactNode
  identifier: string | null
  identifierLabel: string
  users: AbuseRelatedUser[]
  onInvestigate: (email: string) => void
  emptyMessage: string
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {icon}
          {title}
          {users.length > 0 && (
            <Badge variant="destructive" className="text-xs">{users.length}</Badge>
          )}
        </CardTitle>
        {identifier && (
          <CardDescription>
            {identifierLabel}: <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{identifier}</code>
          </CardDescription>
        )}
        {!identifier && (
          <CardDescription className="text-amber-600">
            No {identifierLabel.toLowerCase()} recorded (signed up before tracking was enabled)
          </CardDescription>
        )}
      </CardHeader>
      <CardContent>
        {!identifier || users.length === 0 ? (
          <p className="text-sm text-muted-foreground">{!identifier ? `Cannot check — no ${identifierLabel.toLowerCase()} data` : emptyMessage}</p>
        ) : (
          <div className="border rounded-lg overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-right">Jobs</TableHead>
                  <TableHead className="text-right">Spent</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow key={user.email}>
                    <TableCell className="font-mono text-xs">{user.email}</TableCell>
                    <TableCell className="text-right">{user.total_jobs_created}</TableCell>
                    <TableCell className="text-right">
                      {user.total_spent === 0 ? (
                        <Badge variant="destructive" className="text-xs">$0</Badge>
                      ) : (
                        formatCents(user.total_spent)
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(user.created_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onInvestigate(user.email)}
                        title="Investigate this account"
                      >
                        <ChevronRight className="w-4 h-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// =============================================================================
// Lookup Tab (search by email, IP, or fingerprint)
// =============================================================================

function LookupTab({
  onInvestigate,
}: {
  onInvestigate: (email: string) => void
}) {
  const { toast } = useToast()
  const [query, setQuery] = useState("")
  const [lookupType, setLookupType] = useState<"email" | "ip" | "fingerprint">("email")
  const [results, setResults] = useState<AbuseRelatedUser[] | null>(null)
  const [loading, setLoading] = useState(false)

  const doLookup = async () => {
    if (!query.trim()) return
    try {
      setLoading(true)
      setResults(null)
      if (lookupType === "email") {
        onInvestigate(query.trim())
        setLoading(false)
        return
      } else if (lookupType === "ip") {
        const result = await adminApi.getAbuseByIp(query.trim())
        setResults(result.users)
      } else {
        const result = await adminApi.getAbuseByFingerprint(query.trim())
        setResults(result.users)
      }
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Search className="w-5 h-5" />
          Lookup
        </CardTitle>
        <CardDescription>
          Search by email, IP address, or device fingerprint
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 mb-4">
          <select
            value={lookupType}
            onChange={(e) => {
              setLookupType(e.target.value as "email" | "ip" | "fingerprint")
              setResults(null)
            }}
            className="h-9 rounded-md border bg-background px-3 text-sm"
          >
            <option value="email">Email</option>
            <option value="ip">IP Address</option>
            <option value="fingerprint">Fingerprint</option>
          </select>
          <Input
            placeholder={
              lookupType === "email" ? "user@example.com" :
              lookupType === "ip" ? "1.2.3.4" : "fingerprint-id"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doLookup()}
            className="max-w-sm"
          />
          <Button onClick={doLookup} disabled={loading || !query.trim()} size="sm">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            <span className="ml-1">Search</span>
          </Button>
        </div>

        {results !== null && (
          <>
            <p className="text-sm text-muted-foreground mb-3">
              {results.length} account{results.length !== 1 ? "s" : ""} found
            </p>
            {results.length > 0 && (
              <div className="border rounded-lg overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Email</TableHead>
                      <TableHead className="text-right">Jobs</TableHead>
                      <TableHead className="text-right">Spent</TableHead>
                      <TableHead>IP</TableHead>
                      <TableHead>Fingerprint</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {results.map((user) => (
                      <TableRow key={user.email}>
                        <TableCell className="font-mono text-xs">{user.email}</TableCell>
                        <TableCell className="text-right">{user.total_jobs_created}</TableCell>
                        <TableCell className="text-right">
                          {user.total_spent === 0 ? (
                            <Badge variant="destructive" className="text-xs">$0</Badge>
                          ) : (
                            formatCents(user.total_spent)
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{truncate(user.signup_ip, 15)}</TableCell>
                        <TableCell className="font-mono text-xs">{truncate(user.device_fingerprint, 10)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDate(user.created_at)}
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onInvestigate(user.email)}
                          >
                            <ChevronRight className="w-4 h-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

// =============================================================================
// Main Page
// =============================================================================

export default function AbuseInvestigationPage() {
  const [activeTab, setActiveTab] = useState("suspicious")
  const [investigatingEmail, setInvestigatingEmail] = useState<string | null>(null)

  const handleInvestigate = (email: string) => {
    setInvestigatingEmail(email)
  }

  const handleBack = () => {
    setInvestigatingEmail(null)
  }

  // If investigating a specific user, show the detail view
  if (investigatingEmail) {
    return (
      <RelatedAccountsView
        email={investigatingEmail}
        onBack={handleBack}
        onInvestigate={handleInvestigate}
      />
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Abuse Investigation</h1>
        <p className="text-muted-foreground">
          Find suspicious accounts and investigate cross-account abuse patterns
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="suspicious">
            <AlertTriangle className="w-4 h-4 mr-1" />
            Suspicious
          </TabsTrigger>
          <TabsTrigger value="lookup">
            <Search className="w-4 h-4 mr-1" />
            Lookup
          </TabsTrigger>
        </TabsList>

        <TabsContent value="suspicious" className="space-y-6">
          <SuspiciousAccountsTab onInvestigate={handleInvestigate} />
        </TabsContent>

        <TabsContent value="lookup" className="space-y-6">
          <LookupTab onInvestigate={handleInvestigate} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
