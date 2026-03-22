"use client"

import { useEffect, useState } from "react"
import {
  adminApi,
  BlocklistsResponse,
  YouTubeQueueListResponse,
} from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
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
  Globe,
  Mail,
  Server,
  Youtube,
  RefreshCw,
  Loader2,
  Plus,
  Trash2,
  Search,
  Play,
  RotateCcw,
  Clock,
  CheckCircle2,
  XCircle,
  Shield,
  X,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"
import { IpInfo } from "@/components/admin/ip-info"

export default function AdminRateLimitsPage() {
  const { toast } = useToast()
  const [blocklists, setBlocklists] = useState<BlocklistsResponse | null>(null)
  const [youtubeQueue, setYoutubeQueue] = useState<YouTubeQueueListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [processingQueue, setProcessingQueue] = useState(false)
  const [activeTab, setActiveTab] = useState("youtube-queue")

  // Form states
  const [newDomain, setNewDomain] = useState("")
  const [newEmail, setNewEmail] = useState("")
  const [newIP, setNewIP] = useState("")
  const [searchDomain, setSearchDomain] = useState("")
  const [searchEmail, setSearchEmail] = useState("")
  const [searchIP, setSearchIP] = useState("")
  const [syncing, setSyncing] = useState(false)
  const [newAllowlistDomain, setNewAllowlistDomain] = useState("")
  const [searchAllowlist, setSearchAllowlist] = useState("")
  const [searchExternalDomain, setSearchExternalDomain] = useState("")

  const loadData = async () => {
    try {
      setLoading(true)
      const [blocklistsData, queueData] = await Promise.all([
        adminApi.getBlocklists(),
        adminApi.getYouTubeQueue(),
      ])
      setBlocklists(blocklistsData)
      setYoutubeQueue(queueData)
    } catch (err: any) {
      console.error("Failed to load rate limits data:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load rate limits data",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  // Blocklist actions
  const handleAddDomain = async () => {
    if (!newDomain.trim()) return
    try {
      await adminApi.addDisposableDomain(newDomain.trim())
      toast({ title: "Success", description: `Domain "${newDomain}" added to blocklist` })
      setNewDomain("")
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleRemoveDomain = async (domain: string) => {
    try {
      await adminApi.removeDisposableDomain(domain)
      toast({ title: "Success", description: `Domain "${domain}" removed from blocklist` })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleSync = async () => {
    try {
      setSyncing(true)
      const result = await adminApi.syncDisposableDomains()
      toast({ title: "Success", description: result.message })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    } finally {
      setSyncing(false)
    }
  }

  const handleRemoveExternalDomain = async (domain: string) => {
    try {
      await adminApi.addAllowlistedDomain(domain)
      toast({ title: "Success", description: `Domain "${domain}" added to allowlist` })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleAddAllowlist = async () => {
    if (!newAllowlistDomain.trim()) return
    try {
      await adminApi.addAllowlistedDomain(newAllowlistDomain.trim())
      toast({ title: "Success", description: `Domain "${newAllowlistDomain}" added to allowlist` })
      setNewAllowlistDomain("")
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleRemoveAllowlist = async (domain: string) => {
    try {
      await adminApi.removeAllowlistedDomain(domain)
      toast({ title: "Success", description: `Domain "${domain}" removed from allowlist` })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleAddEmail = async () => {
    if (!newEmail.trim()) return
    try {
      await adminApi.addBlockedEmail(newEmail.trim())
      toast({ title: "Success", description: `Email "${newEmail}" added to blocklist` })
      setNewEmail("")
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleRemoveEmail = async (email: string) => {
    try {
      await adminApi.removeBlockedEmail(email)
      toast({ title: "Success", description: `Email "${email}" removed from blocklist` })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleAddIP = async () => {
    if (!newIP.trim()) return
    try {
      await adminApi.addBlockedIP(newIP.trim())
      toast({ title: "Success", description: `IP "${newIP}" added to blocklist` })
      setNewIP("")
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleRemoveIP = async (ip: string) => {
    try {
      await adminApi.removeBlockedIP(ip)
      toast({ title: "Success", description: `IP "${ip}" removed from blocklist` })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  // YouTube queue actions
  const handleRetryUpload = async (jobId: string) => {
    try {
      await adminApi.retryYouTubeUpload(jobId)
      toast({ title: "Success", description: `Upload for job ${jobId} queued for retry` })
      loadData()
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    }
  }

  const handleProcessQueue = async () => {
    try {
      setProcessingQueue(true)
      await adminApi.processYouTubeQueue()
      toast({ title: "Success", description: "Queue processing started in background" })
      // Refresh after a short delay to show updated state
      setTimeout(loadData, 3000)
    } catch (err: any) {
      toast({ title: "Error", description: err.message, variant: "destructive" })
    } finally {
      setProcessingQueue(false)
    }
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "—"
    const date = new Date(dateStr)
    if (isNaN(date.getTime())) return "—"
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  // Filter functions
  const filteredExternalDomains = blocklists?.external_domains?.filter(d =>
    d.toLowerCase().includes(searchExternalDomain.toLowerCase())
  ) || []

  const filteredManualDomains = blocklists?.manual_domains?.filter(d =>
    d.toLowerCase().includes(searchDomain.toLowerCase())
  ) || []

  const filteredAllowlisted = blocklists?.allowlisted_domains?.filter(d =>
    d.toLowerCase().includes(searchAllowlist.toLowerCase())
  ) || []

  const filteredEmails = blocklists?.blocked_emails?.filter(e =>
    e.toLowerCase().includes(searchEmail.toLowerCase())
  ) || []

  const filteredIPs = blocklists?.blocked_ips.filter(ip =>
    ip.includes(searchIP)
  ) || []

  if (loading && !blocklists) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Rate Limits</h1>
          <p className="text-muted-foreground">
            Manage YouTube upload queue and blocklists
          </p>
        </div>
        <Button onClick={loadData} variant="outline" disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="youtube-queue">
            YouTube Queue
            {(youtubeQueue?.stats?.queued ?? 0) > 0 && (
              <Badge variant="destructive" className="ml-2 text-xs">{youtubeQueue?.stats?.queued}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="blocklists">Blocklists</TabsTrigger>
        </TabsList>

        {/* YouTube Queue Tab */}
        <TabsContent value="youtube-queue" className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Youtube className="w-5 h-5" />
                    YouTube Upload Queue
                  </CardTitle>
                  <CardDescription>
                    Uploads deferred due to API quota limits. Processed automatically every hour.
                  </CardDescription>
                </div>
                <Button
                  onClick={handleProcessQueue}
                  disabled={processingQueue}
                  variant="outline"
                >
                  {processingQueue ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Play className="w-4 h-4 mr-2" />
                  )}
                  Process Queue Now
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {!youtubeQueue?.entries.length ? (
                <p className="text-muted-foreground text-sm py-8 text-center">
                  No YouTube uploads in queue
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job ID</TableHead>
                      <TableHead>Song</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Attempts</TableHead>
                      <TableHead>Queued</TableHead>
                      <TableHead>Error</TableHead>
                      <TableHead className="w-[100px]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {youtubeQueue.entries.map((entry) => (
                      <TableRow key={entry.job_id}>
                        <TableCell className="font-mono text-xs">
                          {entry.job_id.substring(0, 8)}
                        </TableCell>
                        <TableCell>
                          <div className="text-sm">{entry.artist} - {entry.title}</div>
                          <div className="text-xs text-muted-foreground">{entry.brand_code}</div>
                        </TableCell>
                        <TableCell>
                          {entry.status === "queued" && (
                            <Badge variant="secondary" className="flex items-center gap-1 w-fit">
                              <Clock className="w-3 h-3" /> Queued
                            </Badge>
                          )}
                          {entry.status === "processing" && (
                            <Badge variant="default" className="flex items-center gap-1 w-fit">
                              <Loader2 className="w-3 h-3 animate-spin" /> Processing
                            </Badge>
                          )}
                          {entry.status === "completed" && (
                            <Badge variant="default" className="flex items-center gap-1 w-fit bg-green-600">
                              <CheckCircle2 className="w-3 h-3" /> Done
                            </Badge>
                          )}
                          {entry.status === "failed" && (
                            <Badge variant="destructive" className="flex items-center gap-1 w-fit">
                              <XCircle className="w-3 h-3" /> Failed
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-sm">
                          {entry.attempts}/{entry.max_attempts}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDate(entry.queued_at)}
                        </TableCell>
                        <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground" title={entry.last_error || undefined}>
                          {entry.last_error || "—"}
                        </TableCell>
                        <TableCell>
                          {(entry.status === "failed" || entry.status === "queued") && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleRetryUpload(entry.job_id)}
                              title="Retry upload"
                            >
                              <RotateCcw className="w-4 h-4" />
                            </Button>
                          )}
                          {entry.status === "completed" && entry.youtube_url && (
                            <a
                              href={entry.youtube_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-blue-600 hover:underline text-sm"
                            >
                              View
                            </a>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Blocklists Tab */}
        <TabsContent value="blocklists" className="space-y-6">
          {/* Sync Status */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Globe className="w-5 h-5" />
                    Disposable Email Domains
                  </CardTitle>
                  <CardDescription>
                    {blocklists?.last_sync_at
                      ? `Last synced ${formatDate(blocklists.last_sync_at)} — ${blocklists.last_sync_count?.toLocaleString() || 0} external domains`
                      : "Never synced"}
                  </CardDescription>
                </div>
                <Button onClick={handleSync} disabled={syncing} variant="outline">
                  {syncing ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <RefreshCw className="w-4 h-4 mr-2" />
                  )}
                  Sync Now
                </Button>
              </div>
            </CardHeader>
          </Card>

          {/* External Domains */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Globe className="w-5 h-5" />
                External Domains
                <Badge variant="secondary" className="ml-1">{blocklists?.external_domains?.length || 0}</Badge>
              </CardTitle>
              <CardDescription>
                Domains from the external disposable email list. Remove a domain to add it to the allowlist.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  placeholder="Search external domains..."
                  value={searchExternalDomain}
                  onChange={(e) => setSearchExternalDomain(e.target.value)}
                  className="pl-9"
                />
              </div>
              <div className="max-h-64 overflow-y-auto border rounded-md">
                <div className="flex flex-wrap gap-2 p-3">
                  {filteredExternalDomains.slice(0, 200).map((domain) => (
                    <Badge key={domain} variant="outline" className="flex items-center gap-1">
                      {domain}
                      <button
                        onClick={() => handleRemoveExternalDomain(domain)}
                        className="ml-1 hover:text-destructive cursor-pointer"
                        title="Add to allowlist"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </Badge>
                  ))}
                  {filteredExternalDomains.length > 200 && (
                    <span className="text-muted-foreground text-sm">
                      +{filteredExternalDomains.length - 200} more
                    </span>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Manual Domains */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Globe className="w-5 h-5" />
                Manual Domains
                <Badge variant="secondary" className="ml-1">{blocklists?.manual_domains?.length || 0}</Badge>
              </CardTitle>
              <CardDescription>
                Manually added disposable email domains.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    placeholder="Search manual domains..."
                    value={searchDomain}
                    onChange={(e) => setSearchDomain(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <Input
                  placeholder="Add domain (e.g., tempmail.com)"
                  value={newDomain}
                  onChange={(e) => setNewDomain(e.target.value)}
                  className="w-64"
                  onKeyDown={(e) => e.key === "Enter" && handleAddDomain()}
                />
                <Button onClick={handleAddDomain} disabled={!newDomain.trim()}>
                  <Plus className="w-4 h-4 mr-2" />
                  Add
                </Button>
              </div>
              {filteredManualDomains.length === 0 ? (
                <p className="text-muted-foreground text-sm py-4 text-center">
                  No manual domains
                </p>
              ) : (
                <div className="max-h-64 overflow-y-auto border rounded-md">
                  <div className="flex flex-wrap gap-2 p-3">
                    {filteredManualDomains.map((domain) => (
                      <Badge key={domain} variant="secondary" className="flex items-center gap-1">
                        {domain}
                        <button
                          onClick={() => handleRemoveDomain(domain)}
                          className="ml-1 hover:text-destructive cursor-pointer"
                          title="Remove domain"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Allowlisted Domains */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="w-5 h-5" />
                Allowlisted Domains
                <Badge variant="secondary" className="ml-1">{blocklists?.allowlisted_domains?.length || 0}</Badge>
              </CardTitle>
              <CardDescription>
                Domains that override the external blocklist. These domains will not be blocked.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    placeholder="Search allowlisted domains..."
                    value={searchAllowlist}
                    onChange={(e) => setSearchAllowlist(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <Input
                  placeholder="Add domain to allowlist"
                  value={newAllowlistDomain}
                  onChange={(e) => setNewAllowlistDomain(e.target.value)}
                  className="w-64"
                  onKeyDown={(e) => e.key === "Enter" && handleAddAllowlist()}
                />
                <Button onClick={handleAddAllowlist} disabled={!newAllowlistDomain.trim()}>
                  <Plus className="w-4 h-4 mr-2" />
                  Add
                </Button>
              </div>
              {filteredAllowlisted.length === 0 ? (
                <p className="text-muted-foreground text-sm py-4 text-center">
                  No allowlisted domains
                </p>
              ) : (
                <div className="max-h-64 overflow-y-auto border rounded-md">
                  <div className="flex flex-wrap gap-2 p-3">
                    {filteredAllowlisted.map((domain) => (
                      <Badge key={domain} variant="default" className="flex items-center gap-1 bg-green-600">
                        {domain}
                        <button
                          onClick={() => handleRemoveAllowlist(domain)}
                          className="ml-1 hover:text-white cursor-pointer"
                          title="Remove from allowlist"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Blocked Emails */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Mail className="w-5 h-5" />
                Blocked Emails
              </CardTitle>
              <CardDescription>
                Specific email addresses blocked ({filteredEmails.length})
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    placeholder="Search emails..."
                    value={searchEmail}
                    onChange={(e) => setSearchEmail(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <Input
                  placeholder="Add email address"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  className="w-64"
                  onKeyDown={(e) => e.key === "Enter" && handleAddEmail()}
                />
                <Button onClick={handleAddEmail} disabled={!newEmail.trim()}>
                  <Plus className="w-4 h-4 mr-2" />
                  Add
                </Button>
              </div>
              {filteredEmails.length === 0 ? (
                <p className="text-muted-foreground text-sm py-4 text-center">
                  No blocked emails
                </p>
              ) : (
                <div className="max-h-48 overflow-y-auto border rounded-md">
                  <div className="flex flex-wrap gap-2 p-3">
                    {filteredEmails.map((email) => (
                      <Badge key={email} variant="destructive" className="flex items-center gap-1">
                        {email}
                        <button
                          onClick={() => handleRemoveEmail(email)}
                          className="ml-1 hover:text-white cursor-pointer"
                          title="Remove email"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Blocked IPs */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Server className="w-5 h-5" />
                Blocked IP Addresses
              </CardTitle>
              <CardDescription>
                IP addresses blocked from all operations ({filteredIPs.length})
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    placeholder="Search IPs..."
                    value={searchIP}
                    onChange={(e) => setSearchIP(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <Input
                  placeholder="Add IP address"
                  value={newIP}
                  onChange={(e) => setNewIP(e.target.value)}
                  className="w-64"
                  onKeyDown={(e) => e.key === "Enter" && handleAddIP()}
                />
                <Button onClick={handleAddIP} disabled={!newIP.trim()}>
                  <Plus className="w-4 h-4 mr-2" />
                  Add
                </Button>
              </div>
              {filteredIPs.length === 0 ? (
                <p className="text-muted-foreground text-sm py-4 text-center">
                  No blocked IPs
                </p>
              ) : (
                <div className="max-h-48 overflow-y-auto border rounded-md">
                  <div className="flex flex-wrap gap-2 p-3">
                    {filteredIPs.map((ip) => (
                      <Badge key={ip} variant="destructive" className="flex items-center gap-1">
                        <IpInfo ip={ip} compact />
                        <button
                          onClick={() => handleRemoveIP(ip)}
                          className="ml-1 hover:text-white cursor-pointer"
                          title="Remove IP"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

      </Tabs>
    </div>
  )
}
