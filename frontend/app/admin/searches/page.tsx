"use client"

import { useEffect, useState, useCallback } from "react"
import { adminApi, AudioSearchJobSummary } from "@/lib/api"
import { Button } from "@/components/ui/button"
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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  RefreshCw,
  Loader2,
  Trash2,
  Music,
  CheckCircle,
  XCircle,
  ExternalLink,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"

const statusOptions = [
  { value: "all", label: "All Statuses" },
  { value: "awaiting_audio_selection", label: "Awaiting Selection" },
  { value: "pending", label: "Pending" },
  { value: "complete", label: "Complete" },
  { value: "failed", label: "Failed" },
]

export default function AdminSearchesPage() {
  const { toast } = useToast()
  const [searches, setSearches] = useState<AudioSearchJobSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState("all")

  // Clear cache dialog
  const [clearDialogOpen, setClearDialogOpen] = useState(false)
  const [jobToClear, setJobToClear] = useState<AudioSearchJobSummary | null>(null)
  const [clearing, setClearing] = useState(false)

  const loadSearches = useCallback(async () => {
    try {
      setLoading(true)
      const data = await adminApi.listAudioSearches({
        limit: 100,
        status_filter: statusFilter !== "all" ? statusFilter : undefined,
      })
      setSearches(data.jobs)
    } catch (err: any) {
      console.error("Failed to load searches:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load audio searches",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }, [statusFilter, toast])

  useEffect(() => {
    loadSearches()
  }, [loadSearches])

  const handleClearCache = async () => {
    if (!jobToClear) return

    try {
      setClearing(true)
      const result = await adminApi.clearAudioSearchCache(jobToClear.job_id)
      toast({
        title: "Cache Cleared",
        description: result.message,
      })
      setClearDialogOpen(false)
      setJobToClear(null)
      loadSearches()
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to clear cache",
        variant: "destructive",
      })
    } finally {
      setClearing(false)
    }
  }

  const getStatusVariant = (status: string) => {
    if (status === "complete" || status === "prep_complete") return "default"
    if (status === "failed") return "destructive"
    if (status === "cancelled") return "outline"
    if (status.includes("awaiting")) return "secondary"
    return "secondary"
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "—"
    return new Date(dateStr).toLocaleString()
  }

  const getProviderBadgeClass = (provider: string) => {
    switch (provider.toLowerCase()) {
      case "youtube":
        return "bg-red-500/20 text-red-400 border-red-500/30"
      case "red":
        return "bg-red-600/30 text-red-300 border-red-500/30"
      case "ops":
        return "bg-blue-500/20 text-blue-400 border-blue-500/30"
      default:
        return "bg-slate-500/20 text-slate-400 border-slate-500/30"
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Audio Searches</h1>
          <p className="text-muted-foreground">
            View and manage cached audio search results
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadSearches} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-4">
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Filter by status" />
          </SelectTrigger>
          <SelectContent>
            {statusOptions.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Job ID</TableHead>
              <TableHead>Search Query</TableHead>
              <TableHead>Results</TableHead>
              <TableHead>Providers</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>User</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin mx-auto text-muted-foreground" />
                </TableCell>
              </TableRow>
            ) : searches.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                  <Music className="w-8 h-8 mx-auto mb-2 opacity-50" />
                  No audio searches found
                </TableCell>
              </TableRow>
            ) : (
              searches.map((search) => (
                <TableRow key={search.job_id}>
                  <TableCell className="font-mono text-xs">
                    <a
                      href={`/admin/jobs?id=${search.job_id}`}
                      className="text-blue-500 hover:underline"
                    >
                      {search.job_id.slice(0, 8)}...
                    </a>
                  </TableCell>
                  <TableCell>
                    <div className="max-w-[200px]">
                      <div className="font-medium truncate">
                        {search.audio_search_artist || "—"}
                      </div>
                      <div className="text-sm text-muted-foreground truncate">
                        {search.audio_search_title || "—"}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{search.results_count}</span>
                            {search.has_lossless ? (
                              <CheckCircle className="w-4 h-4 text-green-500" />
                            ) : (
                              <XCircle className="w-4 h-4 text-red-400" />
                            )}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>{search.has_lossless ? "Has lossless sources" : "No lossless sources (YouTube only)"}</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {search.providers.map((provider) => (
                        <Badge
                          key={provider}
                          variant="outline"
                          className={`text-[10px] ${getProviderBadgeClass(provider)}`}
                        >
                          {provider}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={getStatusVariant(search.status)}>
                      {search.status.replace(/_/g, " ")}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {search.user_email?.split("@")[0] || "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatDate(search.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-orange-500 hover:text-orange-400 hover:bg-orange-500/10"
                            onClick={() => {
                              setJobToClear(search)
                              setClearDialogOpen(true)
                            }}
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Clear cache & allow re-search</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Summary */}
      {!loading && searches.length > 0 && (
        <div className="text-sm text-muted-foreground">
          Showing {searches.length} jobs with cached search results.
          {searches.filter(s => !s.has_lossless).length > 0 && (
            <span className="ml-2 text-orange-400">
              {searches.filter(s => !s.has_lossless).length} have YouTube-only results (may need cache refresh).
            </span>
          )}
        </div>
      )}

      {/* Clear Cache Dialog */}
      <AlertDialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear Search Cache?</AlertDialogTitle>
            <AlertDialogDescription>
              This will clear the cached search results for job{" "}
              <span className="font-mono">{jobToClear?.job_id}</span> and reset it to pending
              status, allowing a new search to be performed.
              {jobToClear && !jobToClear.has_lossless && (
                <span className="block mt-2 text-orange-400">
                  This job only has YouTube results - clearing the cache may fix this if flacfetch has been updated.
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleClearCache}
              disabled={clearing}
              className="bg-orange-600 hover:bg-orange-500"
            >
              {clearing ? (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              ) : (
                <Trash2 className="w-4 h-4 mr-2" />
              )}
              Clear Cache
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
