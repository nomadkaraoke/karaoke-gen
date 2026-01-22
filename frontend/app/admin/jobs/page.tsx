"use client"

import { useEffect, useState, useCallback, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { adminApi, api, Job, FileInfo, JobFilesResponse, JobUpdateRequest, JobResetResponse, DeleteOutputsResponse } from "@/lib/api"
import { useAdminSettings } from "@/lib/admin-settings"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Search,
  RefreshCw,
  Loader2,
  Trash2,
  ExternalLink,
  ArrowLeft,
  User,
  Clock,
  FileText,
  XCircle,
  Globe,
  Upload,
  Download,
  FolderOpen,
  Pencil,
  Check,
  X,
  Settings,
  RotateCcw,
  CloudOff,
  Terminal,
  Music,
  Mic,
  Sliders,
  AlertTriangle,
  ChevronRight,
  Copy,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"

const statusOptions = [
  { value: "all", label: "All Statuses" },
  { value: "pending", label: "Pending" },
  { value: "downloading", label: "Downloading" },
  { value: "searching_audio", label: "Searching Audio" },
  { value: "awaiting_audio_selection", label: "Awaiting Audio Selection" },
  { value: "separating_stage1", label: "Separating (Stage 1)" },
  { value: "separating_stage2", label: "Separating (Stage 2)" },
  { value: "transcribing", label: "Transcribing" },
  { value: "correcting", label: "Correcting" },
  { value: "awaiting_review", label: "Awaiting Review" },
  { value: "in_review", label: "In Review" },
  { value: "rendering_video", label: "Rendering Video" },
  { value: "awaiting_instrumental_selection", label: "Awaiting Instrumental" },
  { value: "generating_video", label: "Generating Video" },
  { value: "encoding", label: "Encoding" },
  { value: "complete", label: "Complete" },
  { value: "prep_complete", label: "Prep Complete" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
]

// Consistent status label mapping (Title Case, clear naming)
const statusLabels: Record<string, string> = {
  pending: "Pending",
  downloading: "Downloading",
  searching_audio: "Searching",
  awaiting_audio_selection: "Awaiting Audio",
  separating_stage1: "Separating 1",
  separating_stage2: "Separating 2",
  transcribing: "Transcribing",
  correcting: "Correcting",
  awaiting_review: "Awaiting Review",
  in_review: "In Review",
  rendering_video: "Rendering",
  awaiting_instrumental_selection: "Awaiting Inst.",
  generating_video: "Generating",
  encoding: "Encoding",
  complete: "Complete",
  prep_complete: "Prep Complete",
  failed: "Failed",
  cancelled: "Cancelled",
}

// Inner component that uses useSearchParams
function AdminJobsPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { toast } = useToast()
  const { showTestData } = useAdminSettings()

  // Get jobId from query params for detail view
  const selectedJobId = searchParams.get("id")

  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState("all")
  const [userEmailFilter, setUserEmailFilter] = useState("")
  const [searchInput, setSearchInput] = useState("")

  // Detail view state
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [detailLoading, setDetailLoading] = useState(false)
  const [logsLoading, setLogsLoading] = useState(false)
  const [files, setFiles] = useState<FileInfo[]>([])
  const [filesLoading, setFilesLoading] = useState(false)

  // Logs modal state
  const [logsModalOpen, setLogsModalOpen] = useState(false)
  const [logFilter, setLogFilter] = useState<string>("all")
  const [logSearch, setLogSearch] = useState("")

  // Delete dialog
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [jobToDelete, setJobToDelete] = useState<Job | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Editing state
  const [editingField, setEditingField] = useState<string | null>(null)
  const [editValue, setEditValue] = useState("")
  const [saving, setSaving] = useState(false)

  // Reset state
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [resetTarget, setResetTarget] = useState<string | null>(null)
  const [resetting, setResetting] = useState(false)

  // Delete outputs state
  const [deleteOutputsDialogOpen, setDeleteOutputsDialogOpen] = useState(false)
  const [deletingOutputs, setDeletingOutputs] = useState(false)

  const loadJobs = useCallback(async () => {
    try {
      setLoading(true)
      const data = await adminApi.listAllJobs({
        status: statusFilter !== "all" ? statusFilter : undefined,
        user_email: userEmailFilter || undefined,
        limit: 100,
        exclude_test: !showTestData,
      })
      setJobs(data)
    } catch (err: any) {
      console.error("Failed to load jobs:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load jobs",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }, [statusFilter, userEmailFilter, showTestData, toast])

  const loadJobDetail = useCallback(async (jobId: string) => {
    try {
      setDetailLoading(true)
      const data = await api.getJob(jobId)
      setSelectedJob(data)
    } catch (err: any) {
      console.error("Failed to load job:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load job",
        variant: "destructive",
      })
      setSelectedJob(null)
    } finally {
      setDetailLoading(false)
    }
  }, [toast])

  const loadLogs = useCallback(async (jobId: string) => {
    try {
      setLogsLoading(true)
      const data = await api.getJobLogs(jobId, 200)
      setLogs(data)
    } catch (err: any) {
      console.error("Failed to load logs:", err)
    } finally {
      setLogsLoading(false)
    }
  }, [])

  const loadFiles = useCallback(async (jobId: string) => {
    try {
      setFilesLoading(true)
      const data = await adminApi.getJobFiles(jobId)
      setFiles(data.files)
    } catch (err: any) {
      console.error("Failed to load files:", err)
      setFiles([])
    } finally {
      setFilesLoading(false)
    }
  }, [])

  useEffect(() => {
    loadJobs()
  }, [loadJobs])

  useEffect(() => {
    if (selectedJobId) {
      loadJobDetail(selectedJobId)
      loadLogs(selectedJobId)
      loadFiles(selectedJobId)
    } else {
      setSelectedJob(null)
      setLogs([])
      setFiles([])
    }
  }, [selectedJobId, loadJobDetail, loadLogs, loadFiles])

  const handleSearch = () => {
    setUserEmailFilter(searchInput)
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch()
    }
  }

  // Start editing a field
  const startEditing = (field: string, currentValue: string) => {
    setEditingField(field)
    setEditValue(currentValue || "")
  }

  // Cancel editing
  const cancelEditing = () => {
    setEditingField(null)
    setEditValue("")
  }

  // Save edited field
  const saveField = async (field: string) => {
    if (!selectedJobId || !selectedJob) return

    try {
      setSaving(true)
      const updates: JobUpdateRequest = { [field]: editValue }
      await adminApi.updateJob(selectedJobId, updates)

      // Update local state
      setSelectedJob({ ...selectedJob, [field]: editValue })

      toast({
        title: "Updated",
        description: `${field} has been updated`,
      })

      setEditingField(null)
      setEditValue("")
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || `Failed to update ${field}`,
        variant: "destructive",
      })
    } finally {
      setSaving(false)
    }
  }

  // Handle key press in edit input
  const handleEditKeyPress = (e: React.KeyboardEvent, field: string) => {
    if (e.key === "Enter") {
      saveField(field)
    } else if (e.key === "Escape") {
      cancelEditing()
    }
  }

  const handleDelete = async () => {
    if (!jobToDelete) return

    try {
      setDeleting(true)
      await adminApi.deleteJob(jobToDelete.job_id)
      toast({
        title: "Job Deleted",
        description: `Job ${jobToDelete.job_id} has been deleted`,
      })
      setDeleteDialogOpen(false)
      setJobToDelete(null)
      // If we were viewing this job, go back to list
      if (selectedJobId === jobToDelete.job_id) {
        router.push("/admin/jobs")
      }
      loadJobs()
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to delete job",
        variant: "destructive",
      })
    } finally {
      setDeleting(false)
    }
  }

  // Open the reset confirmation dialog
  const openResetDialog = (targetState: string) => {
    setResetTarget(targetState)
    setResetDialogOpen(true)
  }

  // Handle job reset
  const handleReset = async () => {
    if (!selectedJobId || !resetTarget) return

    try {
      setResetting(true)
      const result = await adminApi.resetJob(selectedJobId, resetTarget)
      toast({
        title: "Job Reset",
        description: result.message,
      })
      setResetDialogOpen(false)
      setResetTarget(null)
      // Refresh job details
      loadJobDetail(selectedJobId)
      loadLogs(selectedJobId)
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to reset job",
        variant: "destructive",
      })
    } finally {
      setResetting(false)
    }
  }

  // Handle delete outputs
  const handleDeleteOutputs = async () => {
    if (!selectedJobId) return

    try {
      setDeletingOutputs(true)
      const result = await adminApi.deleteJobOutputs(selectedJobId)

      // Check for errors in individual services
      const hasErrors = result.status === "error" || result.status === "partial_success"
      const serviceResults = Object.entries(result.deleted_services)
        .map(([service, r]: [string, any]) => `${service}: ${r.status}${r.error ? ` (${r.error})` : ''}`)
        .join(", ")

      if (hasErrors) {
        toast({
          title: result.status === "error" ? "Delete Failed" : "Partial Success",
          description: `${result.message}. Services: ${serviceResults}`,
          variant: "destructive",
        })
      } else {
        toast({
          title: "Outputs Deleted",
          description: `${result.message}. Cleared: ${result.cleared_state_data.join(", ") || "none"}`,
        })
      }

      setDeleteOutputsDialogOpen(false)
      // Refresh job details
      loadJobDetail(selectedJobId)
      loadLogs(selectedJobId)
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to delete outputs",
        variant: "destructive",
      })
    } finally {
      setDeletingOutputs(false)
    }
  }

  // Get reset target display info
  const getResetTargetInfo = (targetState: string) => {
    const info: Record<string, { label: string; description: string; icon: any }> = {
      pending: {
        label: "Pending",
        description: "Restart from the beginning. Clears all processing data.",
        icon: RotateCcw,
      },
      awaiting_audio_selection: {
        label: "Audio Selection",
        description: "Re-select audio source. Preserves search results.",
        icon: Music,
      },
      awaiting_review: {
        label: "Lyrics Review",
        description: "Re-review lyrics. Preserves audio stems.",
        icon: Mic,
      },
      awaiting_instrumental_selection: {
        label: "Instrumental",
        description: "Re-select instrumental. Preserves lyrics review.",
        icon: Sliders,
      },
      instrumental_selected: {
        label: "Reprocess Video",
        description: "Re-encode and re-distribute with current settings. Triggers video worker automatically.",
        icon: RefreshCw,
      },
    }
    return info[targetState] || { label: targetState, description: "", icon: Settings }
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

  const formatDateCompact = (dateStr?: string) => {
    if (!dateStr) return "—"
    const d = new Date(dateStr)
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
  }

  const formatTimeOnly = (dateStr?: string) => {
    if (!dateStr) return ""
    const d = new Date(dateStr)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  // Filter logs based on level and search
  const filteredLogs = logs.filter(log => {
    const matchesLevel = logFilter === "all" || log.level?.toUpperCase() === logFilter
    const matchesSearch = !logSearch ||
      log.message?.toLowerCase().includes(logSearch.toLowerCase()) ||
      log.worker?.toLowerCase().includes(logSearch.toLowerCase())
    return matchesLevel && matchesSearch
  })

  const getLogLevelColor = (level: string) => {
    switch (level?.toUpperCase()) {
      case "ERROR":
        return "text-red-500"
      case "WARNING":
        return "text-yellow-500"
      case "INFO":
        return "text-blue-500"
      case "DEBUG":
        return "text-muted-foreground"
      default:
        return "text-foreground"
    }
  }

  // Determine job source
  const getJobSource = (job: Job) => {
    if (job?.url) return { type: "url", icon: Globe, label: "YouTube" }
    if (job?.audio_search_artist || job?.audio_search_title) return { type: "search", icon: Search, label: "Search" }
    if (job?.filename) return { type: "upload", icon: Upload, label: "Upload" }
    return { type: "unknown", icon: FileText, label: "Unknown" }
  }

  // Calculate stage durations from timeline
  const getStageDurations = (job: Job) => {
    if (!job?.timeline || job.timeline.length < 2) return []

    const durations: { status: string; duration: number; startTime: string; message?: string }[] = []
    for (let i = 0; i < job.timeline.length - 1; i++) {
      const current = job.timeline[i]
      const next = job.timeline[i + 1]
      const start = new Date(current.timestamp).getTime()
      const end = new Date(next.timestamp).getTime()
      const durationMs = end - start
      durations.push({
        status: current.status,
        duration: durationMs,
        startTime: current.timestamp,
        message: current.message,
      })
    }
    // Add current/final state
    if (job.timeline.length > 0) {
      const last = job.timeline[job.timeline.length - 1]
      durations.push({
        status: last.status,
        duration: 0,
        startTime: last.timestamp,
        message: last.message,
      })
    }
    return durations
  }

  const formatDuration = (ms: number) => {
    if (ms === 0) return ""
    if (ms < 1000) return `${ms}ms`
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
    const mins = Math.floor(ms / 60000)
    const secs = Math.floor((ms % 60000) / 1000)
    return `${mins}m${secs}s`
  }

  const formatDurationLong = (ms: number) => {
    if (ms === 0) return "0s"
    if (ms < 1000) return `${ms}ms`
    const hours = Math.floor(ms / 3600000)
    const mins = Math.floor((ms % 3600000) / 60000)
    const secs = Math.floor((ms % 60000) / 1000)
    if (hours > 0) return `${hours}h ${mins}m ${secs}s`
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  // Calculate job timing stats from timeline
  const getJobTimingStats = (job: Job) => {
    if (!job?.timeline || job.timeline.length < 2) {
      return { total: 0, processing: 0, waiting: 0 }
    }

    const firstTime = new Date(job.timeline[0].timestamp).getTime()
    const lastTime = new Date(job.timeline[job.timeline.length - 1].timestamp).getTime()
    const total = lastTime - firstTime

    // Calculate waiting time (awaiting_* and in_review states)
    let waiting = 0
    for (let i = 0; i < job.timeline.length - 1; i++) {
      const status = job.timeline[i].status
      if (status.includes("awaiting") || status === "in_review") {
        const start = new Date(job.timeline[i].timestamp).getTime()
        const end = new Date(job.timeline[i + 1].timestamp).getTime()
        waiting += end - start
      }
    }

    const processing = total - waiting
    return { total, processing, waiting }
  }

  // Get timeline stage color based on status
  const getTimelineStageColor = (status: string, isCurrent: boolean) => {
    if (status === "complete" || status === "prep_complete") {
      return "bg-emerald-500"
    }
    if (status === "failed") {
      return "bg-red-500"
    }
    if (status === "cancelled") {
      return "bg-zinc-400"
    }
    if (status.includes("awaiting") || status === "in_review") {
      return isCurrent ? "bg-amber-500 animate-pulse" : "bg-amber-500"
    }
    // Processing stages
    return isCurrent ? "bg-blue-500 animate-pulse" : "bg-blue-500"
  }

  // Get timeline text color
  const getTimelineTextColor = (status: string) => {
    if (status === "complete" || status === "prep_complete") return "text-emerald-600 dark:text-emerald-400"
    if (status === "failed") return "text-red-600 dark:text-red-400"
    if (status === "cancelled") return "text-zinc-500"
    if (status.includes("awaiting") || status === "in_review") return "text-amber-600 dark:text-amber-400"
    return "text-blue-600 dark:text-blue-400"
  }

  // Group files by category for display
  const groupFilesByCategory = (files: FileInfo[]) => {
    const groups: Record<string, FileInfo[]> = {}
    for (const file of files) {
      const category = file.category || "input"
      if (!groups[category]) {
        groups[category] = []
      }
      groups[category].push(file)
    }
    // Sort categories in logical order
    const categoryOrder = ["input", "stems", "lyrics", "screens", "videos", "finals", "packages"]
    const sortedGroups: Record<string, FileInfo[]> = {}
    for (const cat of categoryOrder) {
      if (groups[cat]) {
        sortedGroups[cat] = groups[cat]
      }
    }
    // Add any remaining categories not in the order
    for (const cat of Object.keys(groups)) {
      if (!sortedGroups[cat]) {
        sortedGroups[cat] = groups[cat]
      }
    }
    return sortedGroups
  }

  // Get a friendly label for file categories
  const getCategoryLabel = (category: string) => {
    const labels: Record<string, string> = {
      input: "Input",
      stems: "Audio Stems",
      lyrics: "Lyrics",
      screens: "Screen Images",
      videos: "Preview Videos",
      finals: "Final Outputs",
      packages: "Download Packages",
    }
    return labels[category] || category.charAt(0).toUpperCase() + category.slice(1)
  }

  // Copy job ID to clipboard
  const copyJobId = () => {
    if (selectedJob?.job_id) {
      navigator.clipboard.writeText(selectedJob.job_id)
      toast({ title: "Copied", description: "Job ID copied to clipboard" })
    }
  }

  // If a job is selected, show detail view
  if (selectedJobId) {
    if (detailLoading) {
      return (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (!selectedJob) {
      return (
        <div className="text-center py-12">
          <p className="text-muted-foreground">Job not found</p>
          <Button variant="outline" className="mt-4" onClick={() => router.push("/admin/jobs")}>
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to Jobs
          </Button>
        </div>
      )
    }

    const jobSource = getJobSource(selectedJob)
    const stageDurations = getStageDurations(selectedJob)
    const timingStats = getJobTimingStats(selectedJob)
    const SourceIcon = jobSource.icon
    const canDeleteOutputs = ["complete", "prep_complete", "failed", "cancelled"].includes(selectedJob.status)

    return (
      <TooltipProvider>
        <div className="space-y-4">
          {/* ===== HEADER ===== */}
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3 min-w-0">
              <Button variant="ghost" size="icon" className="shrink-0 mt-0.5" onClick={() => router.push("/admin/jobs")}>
                <ArrowLeft className="w-4 h-4" />
              </Button>
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-xl font-semibold tracking-tight font-mono">
                    {selectedJob.job_id}
                  </h1>
                  <Button variant="ghost" size="icon" className="h-6 w-6" onClick={copyJobId}>
                    <Copy className="w-3 h-3" />
                  </Button>
                  <Badge variant={getStatusVariant(selectedJob.status)} className="text-xs">
                    {selectedJob.status.replace(/_/g, " ")}
                  </Badge>
                  {selectedJob.outputs_deleted_at && (
                    <Badge variant="outline" className="text-orange-600 border-orange-600 text-xs">
                      <CloudOff className="w-3 h-3 mr-1" />
                      Outputs Deleted
                    </Badge>
                  )}
                </div>
                {selectedJob.artist && selectedJob.title && (
                  <p className="text-sm text-muted-foreground mt-0.5 truncate">
                    {selectedJob.artist} - {selectedJob.title}
                  </p>
                )}
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                loadJobDetail(selectedJobId)
                loadLogs(selectedJobId)
                loadFiles(selectedJobId)
              }}
              disabled={detailLoading}
              className="shrink-0"
            >
              <RefreshCw className={`w-4 h-4 ${detailLoading ? "animate-spin" : ""}`} />
            </Button>
          </div>

          {/* ===== STICKY ACTION TOOLBAR ===== */}
          <div className="sticky top-0 z-10 -mx-4 px-4 py-2 bg-background/95 backdrop-blur border-b">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide mr-1">Reset to:</span>
              {[
                { state: "pending", icon: RotateCcw, label: "Start" },
                { state: "awaiting_audio_selection", icon: Music, label: "Audio" },
                { state: "awaiting_review", icon: Mic, label: "Lyrics" },
                { state: "awaiting_instrumental_selection", icon: Sliders, label: "Inst." },
                { state: "instrumental_selected", icon: RefreshCw, label: "Reprocess" },
              ].map(({ state, icon: Icon, label }) => (
                <Tooltip key={state}>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => openResetDialog(state)}
                      disabled={resetting}
                    >
                      <Icon className="w-3 h-3 mr-1" />
                      {label}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="font-medium">{getResetTargetInfo(state).label}</p>
                    <p className="text-xs text-muted-foreground">{getResetTargetInfo(state).description}</p>
                  </TooltipContent>
                </Tooltip>
              ))}

              <Separator orientation="vertical" className="h-5 mx-1" />

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={`h-7 px-2 text-xs ${canDeleteOutputs && !selectedJob.outputs_deleted_at ? "border-orange-500 text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-950" : ""}`}
                    onClick={() => setDeleteOutputsDialogOpen(true)}
                    disabled={deletingOutputs || !canDeleteOutputs || !!selectedJob.outputs_deleted_at}
                  >
                    <CloudOff className="w-3 h-3 mr-1" />
                    Del Outputs
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {selectedJob.outputs_deleted_at
                    ? `Deleted ${formatDateCompact(selectedJob.outputs_deleted_at)}`
                    : !canDeleteOutputs
                      ? "Only for terminal states"
                      : "Delete YouTube, Dropbox, GDrive files"}
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 px-2 text-xs text-destructive border-destructive/50 hover:bg-destructive/10"
                    onClick={() => {
                      setJobToDelete(selectedJob)
                      setDeleteDialogOpen(true)
                    }}
                  >
                    <Trash2 className="w-3 h-3 mr-1" />
                    Delete Job
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Permanently delete job and all files</TooltipContent>
              </Tooltip>

              <div className="flex-1" />

              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => setLogsModalOpen(true)}
              >
                <Terminal className="w-3 h-3 mr-1" />
                Logs ({logs.length})
              </Button>
            </div>
          </div>

          {/* ===== ERROR MESSAGE (if any) ===== */}
          {selectedJob.error_message && (
            <div className="flex items-start gap-3 p-3 rounded-lg bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-900">
              <AlertTriangle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1">
                <p className="text-sm text-red-800 dark:text-red-200">{selectedJob.error_message}</p>
                {selectedJob.error_details && (
                  <pre className="mt-2 text-xs bg-red-100 dark:bg-red-900/50 p-2 rounded overflow-x-auto text-red-700 dark:text-red-300">
                    {JSON.stringify(selectedJob.error_details, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )}

          {/* ===== HORIZONTAL TIMELINE ===== */}
          <div className="py-3 border-b">
            <div className="flex flex-wrap items-start gap-y-2">
              {stageDurations.map((stage, i) => {
                const isLast = i === stageDurations.length - 1
                const isCurrent = isLast
                const bgColor = getTimelineStageColor(stage.status, isCurrent)
                const textColor = getTimelineTextColor(stage.status)
                const label = statusLabels[stage.status] || stage.status.replace(/_/g, " ")
                const duration = formatDuration(stage.duration)
                const time = formatTimeOnly(stage.startTime)

                return (
                  <div key={i} className="flex items-start">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div
                          className={`flex flex-col items-center px-2 py-1 rounded-md cursor-default ${
                            isCurrent ? 'bg-muted/50 ring-1 ring-inset' : ''
                          } ${
                            isCurrent ? (
                              stage.status.includes("awaiting") || stage.status === "in_review" ? 'ring-amber-500/50' :
                              stage.status === "complete" || stage.status === "prep_complete" ? 'ring-emerald-500/50' :
                              stage.status === "failed" ? 'ring-red-500/50' : 'ring-blue-500/50'
                            ) : ''
                          }`}
                        >
                          <div className="flex items-center gap-1.5">
                            <div className={`w-2 h-2 rounded-full ${bgColor}`} />
                            <span className={`text-xs font-medium whitespace-nowrap ${textColor}`}>
                              {label}
                            </span>
                            {duration && (
                              <span className="text-[10px] text-muted-foreground font-mono">
                                {duration}
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] text-muted-foreground font-mono mt-0.5">
                            {time}
                          </span>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        <p className="font-medium">{stage.status.replace(/_/g, " ")}</p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(stage.startTime).toLocaleString()}
                        </p>
                        {stage.message && (
                          <p className="text-xs mt-1 max-w-xs">{stage.message}</p>
                        )}
                      </TooltipContent>
                    </Tooltip>
                    {!isLast && (
                      <ChevronRight className="w-3 h-3 text-muted-foreground/40 mx-0.5 mt-2" />
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ===== JOB INFO GRID ===== */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-4 py-3">
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">User</p>
              <p className="text-sm truncate" title={selectedJob.user_email || "Unknown"}>
                {selectedJob.user_email || "Unknown"}
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Source</p>
              <p className="text-sm flex items-center gap-1.5">
                <SourceIcon className="w-3.5 h-3.5 text-muted-foreground" />
                {jobSource.label}
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Created</p>
              <p className="text-sm" title={formatDate(selectedJob.created_at)}>
                {formatDateCompact(selectedJob.created_at)}
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Progress</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden max-w-[60px]">
                  <div
                    className="h-full bg-primary transition-all"
                    style={{ width: `${selectedJob.progress}%` }}
                  />
                </div>
                <span className="text-sm font-medium">{selectedJob.progress}%</span>
              </div>
            </div>
            {/* Timing stats */}
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Total Time</p>
              <p className="text-sm font-mono">{formatDurationLong(timingStats.total) || "—"}</p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Processing</p>
              <p className="text-sm font-mono text-blue-600 dark:text-blue-400">{formatDurationLong(timingStats.processing) || "—"}</p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Waiting</p>
              <p className="text-sm font-mono text-amber-600 dark:text-amber-400">{formatDurationLong(timingStats.waiting) || "—"}</p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Theme</p>
              <p className="text-sm truncate">{selectedJob.theme_id || "default"}</p>
            </div>
          </div>

          {/* ===== TABBED CONTENT SECTIONS ===== */}
          <Tabs defaultValue="overview" className="w-full">
            <TabsList className="w-full justify-start h-9 bg-muted/50">
              <TabsTrigger value="overview" className="text-xs">
                <Settings className="w-3 h-3 mr-1" />
                Overview
              </TabsTrigger>
              <TabsTrigger value="files" className="text-xs">
                <Download className="w-3 h-3 mr-1" />
                Files ({files.length})
              </TabsTrigger>
              <TabsTrigger value="debug" className="text-xs">
                <Terminal className="w-3 h-3 mr-1" />
                Debug
              </TabsTrigger>
            </TabsList>

            {/* Overview Tab - Config + Metadata side by side */}
            <TabsContent value="overview" className="mt-3">
              <div className="grid gap-4 md:grid-cols-2">
                {/* Config Section */}
                <Card>
                  <CardHeader className="py-2 px-3 bg-muted/30">
                    <CardTitle className="text-xs font-medium">Configuration</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-3 space-y-2">
                    {[
                      { key: "artist", label: "Artist", value: selectedJob.artist },
                      { key: "title", label: "Title", value: selectedJob.title },
                      { key: "user_email", label: "User Email", value: selectedJob.user_email },
                      { key: "theme_id", label: "Theme", value: selectedJob.theme_id || "default" },
                      { key: "brand_prefix", label: "Brand Prefix", value: selectedJob.brand_prefix },
                      { key: "customer_email", label: "Customer Email", value: selectedJob.customer_email },
                    ].map(({ key, label, value }) => (
                      <div key={key} className="flex items-center gap-2 group">
                        <span className="text-[10px] font-medium text-muted-foreground uppercase w-20 shrink-0">{label}</span>
                        {editingField === key ? (
                          <div className="flex items-center gap-1 flex-1">
                            <Input
                              value={editValue}
                              onChange={(e) => setEditValue(e.target.value)}
                              onKeyDown={(e) => handleEditKeyPress(e, key)}
                              className="h-6 text-xs"
                              autoFocus
                            />
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => saveField(key)} disabled={saving}>
                              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3 text-green-600" />}
                            </Button>
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={cancelEditing} disabled={saving}>
                              <X className="w-3 h-3 text-red-600" />
                            </Button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-1 flex-1 min-w-0">
                            <span className="text-xs truncate flex-1">{value || "—"}</span>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-5 w-5 opacity-0 group-hover:opacity-100"
                              onClick={() => startEditing(key, value || "")}
                            >
                              <Pencil className="w-2.5 h-2.5" />
                            </Button>
                          </div>
                        )}
                      </div>
                    ))}
                  </CardContent>
                </Card>

                {/* Metadata Section */}
                <Card>
                  <CardHeader className="py-2 px-3 bg-muted/30">
                    <CardTitle className="text-xs font-medium">Request Metadata</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-3 space-y-2">
                    {selectedJob.request_metadata && Object.keys(selectedJob.request_metadata).length > 0 ? (
                      <>
                        {selectedJob.request_metadata.environment && (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-medium text-muted-foreground uppercase w-20 shrink-0">Environment</span>
                            <span className="text-xs">{selectedJob.request_metadata.environment}</span>
                          </div>
                        )}
                        {selectedJob.request_metadata.client_ip && (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-medium text-muted-foreground uppercase w-20 shrink-0">Client IP</span>
                            <span className="text-xs font-mono">{selectedJob.request_metadata.client_ip}</span>
                          </div>
                        )}
                        {selectedJob.request_metadata.user_agent && (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-medium text-muted-foreground uppercase w-20 shrink-0">User Agent</span>
                            <span className="text-xs truncate" title={selectedJob.request_metadata.user_agent}>
                              {selectedJob.request_metadata.user_agent}
                            </span>
                          </div>
                        )}
                        {selectedJob.request_metadata.server_version && (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-medium text-muted-foreground uppercase w-20 shrink-0">Server Ver</span>
                            <span className="text-xs font-mono">{selectedJob.request_metadata.server_version}</span>
                          </div>
                        )}
                        {selectedJob.request_metadata.created_from && (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-medium text-muted-foreground uppercase w-20 shrink-0">Created From</span>
                            <span className="text-xs">{selectedJob.request_metadata.created_from}</span>
                          </div>
                        )}
                      </>
                    ) : (
                      <p className="text-muted-foreground text-xs">No metadata available</p>
                    )}
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            {/* Files Tab */}
            <TabsContent value="files" className="mt-3">
              {filesLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                </div>
              ) : files.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-muted-foreground text-sm">No files available</p>
                  <p className="text-muted-foreground text-xs mt-1">
                    {selectedJob.outputs_deleted_at
                      ? `Outputs were deleted on ${formatDateCompact(selectedJob.outputs_deleted_at)}`
                      : selectedJob.status === "complete" || selectedJob.status === "prep_complete"
                        ? "Files may have been cleaned up or deleted"
                        : "Files will appear once the job progresses"}
                  </p>
                </div>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(groupFilesByCategory(files)).map(([category, categoryFiles]) => (
                    <Card key={category} className="overflow-hidden">
                      <CardHeader className="py-2 px-3 bg-muted/30">
                        <CardTitle className="text-xs font-medium flex items-center gap-2">
                          <FolderOpen className="w-3 h-3" />
                          {getCategoryLabel(category)}
                          <Badge variant="secondary" className="text-[10px] h-4 px-1">
                            {categoryFiles.length}
                          </Badge>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="p-2 space-y-1">
                        {categoryFiles.map((file, idx) => (
                          <a
                            key={`${file.path}-${idx}`}
                            href={file.download_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            download={file.name}
                            className="flex items-center gap-2 p-1.5 rounded hover:bg-muted/50 transition-colors group"
                          >
                            <Download className="w-3 h-3 text-muted-foreground group-hover:text-foreground transition-colors" />
                            <span className="text-xs truncate flex-1" title={file.name}>
                              {file.name}
                            </span>
                          </a>
                        ))}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* Debug Tab */}
            <TabsContent value="debug" className="mt-3">
              <Accordion type="single" collapsible className="w-full">
                <AccordionItem value="state">
                  <AccordionTrigger className="text-sm">State Data</AccordionTrigger>
                  <AccordionContent>
                    <ScrollArea className="h-[300px]">
                      <pre className="text-xs bg-muted p-3 rounded-md overflow-x-auto">
                        {JSON.stringify(selectedJob.state_data || {}, null, 2)}
                      </pre>
                    </ScrollArea>
                  </AccordionContent>
                </AccordionItem>
                <AccordionItem value="file-urls">
                  <AccordionTrigger className="text-sm">File URLs (Raw)</AccordionTrigger>
                  <AccordionContent>
                    <ScrollArea className="h-[300px]">
                      <pre className="text-xs bg-muted p-3 rounded-md overflow-x-auto">
                        {JSON.stringify(selectedJob.file_urls || {}, null, 2)}
                      </pre>
                    </ScrollArea>
                  </AccordionContent>
                </AccordionItem>
                <AccordionItem value="raw">
                  <AccordionTrigger className="text-sm">Raw Job Data</AccordionTrigger>
                  <AccordionContent>
                    <ScrollArea className="h-[400px]">
                      <pre className="text-xs bg-muted p-3 rounded-md overflow-x-auto">
                        {JSON.stringify(selectedJob, null, 2)}
                      </pre>
                    </ScrollArea>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </TabsContent>
          </Tabs>

          {/* ===== LOGS MODAL ===== */}
          <Dialog open={logsModalOpen} onOpenChange={setLogsModalOpen}>
            <DialogContent className="max-w-5xl max-h-[85vh] flex flex-col">
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  <Terminal className="w-4 h-4" />
                  Worker Logs
                  <Badge variant="secondary" className="text-xs">
                    {filteredLogs.length}{filteredLogs.length !== logs.length ? ` / ${logs.length}` : ""} entries
                  </Badge>
                  {logsLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                </DialogTitle>
              </DialogHeader>

              {/* Filter Controls */}
              <div className="flex items-center gap-3 py-2 border-y bg-muted/30 -mx-6 px-6">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Level:</span>
                  <Select value={logFilter} onValueChange={setLogFilter}>
                    <SelectTrigger className="h-7 w-[100px] text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All</SelectItem>
                      <SelectItem value="ERROR">Error</SelectItem>
                      <SelectItem value="WARNING">Warning</SelectItem>
                      <SelectItem value="INFO">Info</SelectItem>
                      <SelectItem value="DEBUG">Debug</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex-1 max-w-xs">
                  <Input
                    placeholder="Search logs..."
                    value={logSearch}
                    onChange={(e) => setLogSearch(e.target.value)}
                    className="h-7 text-xs"
                  />
                </div>
                {(logFilter !== "all" || logSearch) && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => { setLogFilter("all"); setLogSearch(""); }}
                  >
                    Clear filters
                  </Button>
                )}
              </div>

              {/* Log entries - fixed height container for scrolling */}
              <div className="h-[60vh] -mx-6 overflow-auto">
                <div className="font-mono text-xs px-6">
                  {filteredLogs.length > 0 ? (
                    filteredLogs.map((log, i) => (
                      <div key={i} className="flex gap-2 py-1.5 border-b border-muted/20 hover:bg-muted/30">
                        <span className="text-muted-foreground whitespace-nowrap w-20 shrink-0">
                          {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : "—"}
                        </span>
                        <span className={`w-16 shrink-0 ${getLogLevelColor(log.level)}`}>
                          [{log.level}]
                        </span>
                        <span className="text-muted-foreground w-16 shrink-0 truncate">
                          {log.worker || "—"}
                        </span>
                        <span className="flex-1 break-all whitespace-pre-wrap">{log.message}</span>
                      </div>
                    ))
                  ) : (
                    <p className="text-muted-foreground py-8 text-center">
                      {logs.length > 0 ? "No logs match the current filters" : "No logs available"}
                    </p>
                  )}
                </div>
              </div>
            </DialogContent>
          </Dialog>

          {/* ===== DIALOGS ===== */}
          {/* Delete Confirmation Dialog */}
          <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete Job</AlertDialogTitle>
                <AlertDialogDescription>
                  Are you sure you want to delete job {jobToDelete?.job_id}? This action
                  cannot be undone and will also delete all associated files.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleDelete}
                  disabled={deleting}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  {deleting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  Delete
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Reset Confirmation Dialog */}
          <AlertDialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Reset Job</AlertDialogTitle>
                <AlertDialogDescription>
                  {resetTarget && (
                    <>
                      Reset job <span className="font-mono">{selectedJobId}</span> to{" "}
                      <span className="font-semibold">{getResetTargetInfo(resetTarget).label}</span>?
                      <br /><br />
                      {getResetTargetInfo(resetTarget).description}
                      <br /><br />
                      This will update the job status and clear relevant state data.
                    </>
                  )}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleReset}
                  disabled={resetting}
                >
                  {resetting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  Reset
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Delete Outputs Confirmation Dialog */}
          <AlertDialog open={deleteOutputsDialogOpen} onOpenChange={setDeleteOutputsDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle className="flex items-center gap-2">
                  <CloudOff className="w-5 h-5 text-orange-500" />
                  Delete Job Outputs
                </AlertDialogTitle>
                <AlertDialogDescription>
                  This will permanently delete:
                  <ul className="list-disc list-inside mt-2 space-y-1">
                    <li>YouTube video (if uploaded)</li>
                    <li>Dropbox folder (frees brand code for reuse)</li>
                    <li>Google Drive files (if uploaded)</li>
                  </ul>
                  <br />
                  The job record will be preserved with a timestamp marking when outputs were deleted.
                  <br /><br />
                  <strong>This action cannot be undone.</strong>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleDeleteOutputs}
                  disabled={deletingOutputs}
                  className="bg-orange-500 hover:bg-orange-600"
                >
                  {deletingOutputs && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  Delete Outputs
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </TooltipProvider>
    )
  }

  // List view
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Jobs</h1>
          <p className="text-muted-foreground">
            View and manage all jobs in the system
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadJobs} disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        <div className="flex-1 flex gap-2">
          <Input
            placeholder="Filter by user email..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={handleKeyPress}
            className="max-w-sm"
          />
          <Button variant="secondary" onClick={handleSearch}>
            <Search className="w-4 h-4" />
          </Button>
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Filter by status" />
          </SelectTrigger>
          <SelectContent>
            {statusOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
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
              <TableHead>User</TableHead>
              <TableHead>Artist / Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin mx-auto text-muted-foreground" />
                </TableCell>
              </TableRow>
            ) : jobs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                  No jobs found
                </TableCell>
              </TableRow>
            ) : (
              jobs.map((job) => (
                <TableRow
                  key={job.job_id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => router.push(`/admin/jobs?id=${job.job_id}`)}
                >
                  <TableCell className="font-mono text-sm">{job.job_id}</TableCell>
                  <TableCell className="text-sm text-muted-foreground max-w-[150px] truncate">
                    {job.user_email || "—"}
                  </TableCell>
                  <TableCell>
                    {job.artist && job.title ? (
                      <span className="max-w-[200px] truncate block">
                        {job.artist} - {job.title}
                      </span>
                    ) : job.filename ? (
                      <span className="max-w-[200px] truncate block text-muted-foreground">
                        {job.filename}
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={getStatusVariant(job.status)}>
                      {job.status.replace(/_/g, " ")}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-2 bg-muted rounded-full overflow-hidden">
                        <div
                          className="h-full bg-primary transition-all"
                          style={{ width: `${job.progress}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {job.progress}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-sm">
                    {formatDate(job.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => router.push(`/admin/jobs?id=${job.job_id}`)}
                        title="View details"
                      >
                        <ExternalLink className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          setJobToDelete(job)
                          setDeleteDialogOpen(true)
                        }}
                        title="Delete job"
                        className="text-destructive hover:text-destructive"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Summary */}
      <p className="text-sm text-muted-foreground">
        Showing {jobs.length} jobs
        {statusFilter !== "all" && ` with status "${statusFilter}"`}
        {userEmailFilter && ` for user "${userEmailFilter}"`}
      </p>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Job</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete job {jobToDelete?.job_id}? This action
              cannot be undone and will also delete all associated files.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// Wrap in Suspense for useSearchParams
export default function AdminJobsPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    }>
      <AdminJobsPageContent />
    </Suspense>
  )
}
