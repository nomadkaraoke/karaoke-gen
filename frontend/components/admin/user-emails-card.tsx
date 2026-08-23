"use client"

/**
 * UserEmailsCard — "Emails sent to this user" section for the admin user-detail page.
 *
 * Lists every email ever sent to the user (Postmark Messages API for the last ~45 days,
 * merged with our persisted email_log for older mail). Clicking a row opens a modal that
 * renders the email exactly as it appeared in the inbox (sandboxed iframe) alongside its
 * delivery metadata.
 */
import { useEffect, useState } from "react"
import { adminApi, UserEmailSummary, EmailDetail } from "@/lib/api"
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Mail, RefreshCw, Loader2 } from "lucide-react"

function formatDate(value?: string | null): string {
  if (!value) return "—"
  const d = new Date(value)
  return isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}

function statusVariant(status?: string | null): "default" | "secondary" | "destructive" | "outline" {
  const s = (status || "").toLowerCase()
  if (s.includes("bounce") || s.includes("fail")) return "destructive"
  if (s.includes("delivered") || s.includes("sent") || s.includes("opened")) return "default"
  return "secondary"
}

function joinAddrs(value?: string | string[] | null): string {
  if (!value) return ""
  return Array.isArray(value) ? value.join(", ") : value
}

export function UserEmailsCard({ email }: { email: string }) {
  const [emails, setEmails] = useState<UserEmailSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [postmarkAvailable, setPostmarkAvailable] = useState(true)

  const [selected, setSelected] = useState<UserEmailSummary | null>(null)
  const [detail, setDetail] = useState<EmailDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const load = async () => {
    if (!email) return
    try {
      setLoading(true)
      setError(null)
      const data = await adminApi.getUserEmails(email)
      setEmails(data.emails || [])
      setPostmarkAvailable(data.postmark_available)
    } catch (err: any) {
      setError(err?.message || "Failed to load emails")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email])

  const openEmail = async (item: UserEmailSummary) => {
    setSelected(item)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    try {
      // Prefer the Postmark record for rich metadata; fall back to our stored
      // copy for messages older than Postmark's retention window.
      const source = item.source === "log" ? "log" : "postmark"
      const id = item.message_id || item.doc_id || ""
      const data = await adminApi.getEmailDetail(id, source)
      setDetail(data)
    } catch (err: any) {
      setDetailError(err?.message || "Failed to load email")
    } finally {
      setDetailLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Mail className="w-4 h-4" /> Emails Sent
            {emails.length > 0 && <Badge variant="secondary">{emails.length}</Badge>}
          </CardTitle>
          <CardDescription>
            Every email sent to this user — click to view it exactly as they received it.
            {!postmarkAvailable && " (Postmark unavailable — showing stored log only.)"}
          </CardDescription>
        </div>
        <Button variant="ghost" size="icon" onClick={load} disabled={loading} title="Refresh">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : loading && emails.length === 0 ? (
          <p className="text-sm text-muted-foreground">Loading emails…</p>
        ) : emails.length === 0 ? (
          <p className="text-sm text-muted-foreground">No emails found for this user.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Sent</TableHead>
                <TableHead>Subject</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {emails.map((item, i) => (
                <TableRow
                  key={item.message_id || item.doc_id || i}
                  className="cursor-pointer hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                  role="button"
                  tabIndex={0}
                  aria-label={`Open email: ${item.subject || "(no subject)"}`}
                  onClick={() => openEmail(item)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      openEmail(item)
                    }
                  }}
                >
                  <TableCell className="text-sm whitespace-nowrap">{formatDate(item.sent_at)}</TableCell>
                  <TableCell className="text-sm max-w-[280px] truncate">{item.subject || "(no subject)"}</TableCell>
                  <TableCell>
                    {item.email_type ? (
                      <Badge variant="outline" className="text-[10px]">{item.email_type}</Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {item.status ? (
                      <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">
                        {item.source === "log" ? "logged" : "—"}
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="truncate">{selected?.subject || "Email"}</DialogTitle>
            <DialogDescription>
              Rendered exactly as it appeared in the recipient&apos;s inbox.
            </DialogDescription>
          </DialogHeader>

          {detailLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading email…
            </div>
          ) : detailError ? (
            <p className="text-sm text-destructive py-4">{detailError}</p>
          ) : detail ? (
            <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4 overflow-hidden">
              {/* Metadata side panel */}
              <div className="text-xs space-y-2 overflow-y-auto pr-1">
                <MetaRow label="To" value={joinAddrs(detail.to)} />
                <MetaRow label="From" value={detail.from_email} />
                {joinAddrs(detail.cc) && <MetaRow label="Cc" value={joinAddrs(detail.cc)} />}
                {joinAddrs(detail.bcc) && <MetaRow label="Bcc" value={joinAddrs(detail.bcc)} />}
                <MetaRow label="Sent" value={formatDate(detail.sent_at)} />
                {detail.status && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-muted-foreground">Status</span>
                    <Badge variant={statusVariant(detail.status)} className="w-fit">{detail.status}</Badge>
                  </div>
                )}
                {detail.delivered_at && <MetaRow label="Delivered" value={formatDate(detail.delivered_at)} />}
                {typeof detail.open_count === "number" && <MetaRow label="Opens" value={String(detail.open_count)} />}
                {typeof detail.click_count === "number" && <MetaRow label="Clicks" value={String(detail.click_count)} />}
                {detail.email_type && <MetaRow label="Type" value={detail.email_type} />}
                {detail.bounce && Object.keys(detail.bounce).length > 0 && (
                  <MetaRow label="Bounce" value={detail.bounce.Type || detail.bounce.Description || JSON.stringify(detail.bounce)} />
                )}
                {detail.source === "log" && (
                  <p className="text-muted-foreground italic pt-1">
                    From our stored copy (older than Postmark&apos;s 45-day retention).
                  </p>
                )}
              </div>

              {/* Email body rendered in an isolated sandboxed iframe */}
              <div className="min-h-0 overflow-hidden border rounded-md bg-white">
                {detail.html_body ? (
                  <iframe
                    title="Email preview"
                    sandbox=""
                    srcDoc={detail.html_body}
                    className="w-full h-[60vh]"
                  />
                ) : detail.text_body ? (
                  <pre className="w-full h-[60vh] overflow-auto p-4 text-xs whitespace-pre-wrap text-black">
                    {detail.text_body}
                  </pre>
                ) : (
                  <p className="p-4 text-sm text-muted-foreground">No body content available.</p>
                )}
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </Card>
  )
}

function MetaRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-words">{value || "—"}</span>
    </div>
  )
}
