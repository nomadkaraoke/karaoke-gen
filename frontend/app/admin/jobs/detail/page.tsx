"use client"

import { useEffect, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { api, adminApi, Job } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  ArrowLeft,
  RefreshCw,
  Loader2,
  Trash2,
  User,
  Clock,
  FileText,
  XCircle,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"

export default function AdminJobDetailPage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const { toast } = useToast()
  const jobId = searchParams.get("id") || ""

  const [job, setJob] = useState<Job | null>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [logsLoading, setLogsLoading] = useState(false)

  // Delete dialog
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const loadJob = async () => {
    if (!jobId) {
      setLoading(false)
      return
    }
    try {
      setLoading(true)
      const data = await api.getJob(jobId)
      setJob(data)
    } catch (err: any) {
      console.error("Failed to load job:", err)
      toast({
        title: "Error",
        description: err.message || "Failed to load job",
        variant: "destructive",
      })
    } finally {
      setLoading(false)
    }
  }

  const loadLogs = async () => {
    if (!jobId) return
    try {
      setLogsLoading(true)
      const data = await api.getJobLogs(jobId, 200)
      setLogs(data)
    } catch (err: any) {
      console.error("Failed to load logs:", err)
    } finally {
      setLogsLoading(false)
    }
  }

  useEffect(() => {
    loadJob()
    loadLogs()
  }, [jobId])

  const handleDelete = async () => {
    try {
      setDeleting(true)
      await adminApi.deleteJob(jobId)
      toast({
        title: "Job Deleted",
        description: `Job ${jobId} has been deleted`,
      })
      router.push("/admin/jobs")
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

  const getStatusVariant = (status: string) => {
    if (status === "complete" || status === "prep_complete") return "default"
    if (status === "failed") return "destructive"
    if (status === "cancelled") return "outline"
    if (status.includes("awaiting")) return "secondary"
    return "secondary"
  }

  const getLogLevelColor = (level: string) => {
    switch (level?.toUpperCase()) {
      case "ERROR":
        return "text-red-500"
      case "WARNING":
        return "text-yellow-500"
      case "INFO":
        return "text-blue-500"
      case "DEBUG":
        return "text-gray-500"
      default:
        return "text-foreground"
    }
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "—"
    return new Date(dateStr).toLocaleString()
  }

  if (!jobId) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">No job ID provided</p>
        <Button variant="outline" className="mt-4" onClick={() => router.push("/admin/jobs")}>
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Jobs
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

  if (!job) {
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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => router.push("/admin/jobs")}>
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              Job {job.job_id}
              <Badge variant={getStatusVariant(job.status)}>
                {job.status.replace(/_/g, " ")}
              </Badge>
            </h1>
            {job.artist && job.title && (
              <p className="text-muted-foreground">{job.artist} - {job.title}</p>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { loadJob(); loadLogs(); }} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setDeleteDialogOpen(true)}
          >
            <Trash2 className="w-4 h-4 mr-2" />
            Delete
          </Button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <User className="w-4 h-4" />
              User
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm truncate">
              {job.user_email || "Unknown"}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="w-4 h-4" />
              Created
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{formatDate(job.created_at)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="w-4 h-4" />
              Updated
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{formatDate(job.updated_at)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              Progress
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${job.progress}%` }}
                />
              </div>
              <span className="text-sm font-medium">{job.progress}%</span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Error Message */}
      {job.error_message && (
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              <XCircle className="w-5 h-5" />
              Error
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{job.error_message}</p>
          </CardContent>
        </Card>
      )}

      {/* Accordion Sections */}
      <Accordion type="multiple" className="w-full" defaultValue={["timeline", "logs"]}>
        {/* Timeline */}
        <AccordionItem value="timeline">
          <AccordionTrigger>
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4" />
              Timeline ({job.timeline?.length || 0} events)
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {job.timeline && job.timeline.length > 0 ? (
                job.timeline.map((event, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 text-sm border-l-2 border-muted pl-4 py-1"
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">
                          {event.status}
                        </Badge>
                        {event.progress !== undefined && (
                          <span className="text-muted-foreground">
                            {event.progress}%
                          </span>
                        )}
                      </div>
                      {event.message && (
                        <p className="text-muted-foreground mt-1">{event.message}</p>
                      )}
                    </div>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {formatDate(event.timestamp)}
                    </span>
                  </div>
                ))
              ) : (
                <p className="text-muted-foreground text-sm">No timeline events</p>
              )}
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Worker Logs */}
        <AccordionItem value="logs">
          <AccordionTrigger>
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4" />
              Worker Logs ({logs.length} entries)
              {logsLoading && <Loader2 className="w-4 h-4 animate-spin ml-2" />}
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="space-y-1 max-h-[500px] overflow-y-auto font-mono text-xs">
              {logs.length > 0 ? (
                logs.map((log, i) => (
                  <div key={i} className="flex gap-2 py-0.5 border-b border-muted/50">
                    <span className="text-muted-foreground whitespace-nowrap">
                      {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : "—"}
                    </span>
                    <span className={`w-16 ${getLogLevelColor(log.level)}`}>
                      [{log.level}]
                    </span>
                    <span className="text-muted-foreground w-20">
                      {log.worker || "—"}
                    </span>
                    <span className="flex-1 break-all">{log.message}</span>
                  </div>
                ))
              ) : (
                <p className="text-muted-foreground">No logs available</p>
              )}
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* State Data */}
        <AccordionItem value="state">
          <AccordionTrigger>
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4" />
              State Data
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="text-xs bg-muted p-4 rounded-md overflow-x-auto max-h-[400px]">
              {JSON.stringify(job.state_data || {}, null, 2)}
            </pre>
          </AccordionContent>
        </AccordionItem>

        {/* File URLs */}
        <AccordionItem value="files">
          <AccordionTrigger>
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4" />
              File URLs
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="text-xs bg-muted p-4 rounded-md overflow-x-auto max-h-[400px]">
              {JSON.stringify(job.file_urls || {}, null, 2)}
            </pre>
          </AccordionContent>
        </AccordionItem>

        {/* Raw Job Data */}
        <AccordionItem value="raw">
          <AccordionTrigger>
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4" />
              Raw Job Data
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="text-xs bg-muted p-4 rounded-md overflow-x-auto max-h-[500px]">
              {JSON.stringify(job, null, 2)}
            </pre>
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Job</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete job {jobId}? This action cannot be
              undone and will also delete all associated files.
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
