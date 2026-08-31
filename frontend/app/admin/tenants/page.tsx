"use client"

import { useEffect, useState, useCallback } from "react"
import { adminApi, TenantSummary, TenantCreateResult } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import {
  Building2, Plus, RefreshCw, Loader2, Copy, Check, ExternalLink,
} from "lucide-react"
import { useToast } from "@/hooks/use-toast"

// --- Colour field -----------------------------------------------------------
function ColorField({
  label, value, onChange, placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder: string
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} swatch`}
          value={/^#[0-9a-fA-F]{6}$/.test(value) ? value : "#000000"}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-9 cursor-pointer rounded border border-input bg-transparent p-0.5"
        />
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="font-mono"
        />
      </div>
    </div>
  )
}

const EMPTY_FORM = {
  name: "",
  tenant_id: "",
  subdomain: "",
  allowed_email_domains: "",
  artist_color: "",
  title_color: "",
  sung_lyrics_color: "",
  unsung_lyrics_color: "",
  tagline: "",
  distribution_mode: "download_only",
  dropbox_path: "",
  brand_prefix: "",
}

function slugify(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
}

export default function AdminTenantsPage() {
  const { toast } = useToast()
  const [tenants, setTenants] = useState<TenantSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [idEdited, setIdEdited] = useState(false)
  const [karaokeBg, setKaraokeBg] = useState<File | null>(null)
  const [introBg, setIntroBg] = useState<File | null>(null)
  const [logo, setLogo] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [created, setCreated] = useState<TenantCreateResult | null>(null)
  const [copied, setCopied] = useState(false)

  const loadTenants = useCallback(async () => {
    try {
      setLoading(true)
      const data = await adminApi.listTenants()
      setTenants(data.tenants)
    } catch (err: any) {
      toast({ title: "Error", description: err.message || "Failed to load tenants", variant: "destructive" })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { loadTenants() }, [loadTenants])

  const set = (key: keyof typeof EMPTY_FORM, value: string) => {
    setForm((f) => {
      const next = { ...f, [key]: value }
      // Auto-derive the id/subdomain from the name until the admin edits the id
      if (key === "name" && !idEdited) {
        const slug = slugify(value)
        next.tenant_id = slug
        next.subdomain = slug ? `${slug}.nomadkaraoke.com` : ""
      }
      if (key === "tenant_id") {
        setIdEdited(true)
        next.subdomain = value ? `${value}.nomadkaraoke.com` : ""
      }
      return next
    })
  }

  const resetForm = () => {
    setForm({ ...EMPTY_FORM })
    setIdEdited(false)
    setKaraokeBg(null)
    setIntroBg(null)
    setLogo(null)
    setCreated(null)
  }

  const openCreate = () => {
    resetForm()
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      toast({ title: "Name required", description: "Enter a tenant name.", variant: "destructive" })
      return
    }
    setSubmitting(true)
    try {
      const fd = new FormData()
      const fields: (keyof typeof EMPTY_FORM)[] = [
        "name", "tenant_id", "subdomain", "allowed_email_domains",
        "artist_color", "title_color", "sung_lyrics_color", "unsung_lyrics_color",
        "tagline", "distribution_mode", "dropbox_path", "brand_prefix",
      ]
      for (const key of fields) {
        const v = form[key]?.trim?.() ?? form[key]
        if (v) fd.append(key, v as string)
      }
      if (karaokeBg) fd.append("karaoke_background", karaokeBg)
      if (introBg) fd.append("intro_background", introBg)
      if (logo) fd.append("logo", logo)

      const result = await adminApi.createTenant(fd)
      setCreated(result)
      toast({ title: "Tenant created", description: `${result.tenant.name} is ready.` })
      loadTenants()
    } catch (err: any) {
      toast({ title: "Failed to create tenant", description: err.message || "Unknown error", variant: "destructive" })
    } finally {
      setSubmitting(false)
    }
  }

  const copyPreview = async () => {
    if (!created) return
    await navigator.clipboard.writeText(created.preview_url)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Building2 className="h-6 w-6 text-primary" />
          <div>
            <h1 className="text-2xl font-semibold">Tenants</h1>
            <p className="text-sm text-muted-foreground">
              White-label portals. Create one to batch-produce an album with a shared theme and
              client-supplied instrumentals.
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="icon" onClick={loadTenants} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" /> Create tenant
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All tenants</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : tenants.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">
              No tenants yet. Create one to get started.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>ID</TableHead>
                  <TableHead>Subdomain</TableHead>
                  <TableHead>Delivery</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tenants.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell className="font-mono text-xs">{t.id}</TableCell>
                    <TableCell className="text-xs">{t.subdomain}</TableCell>
                    <TableCell className="text-xs">{t.dropbox_path || "download only"}</TableCell>
                    <TableCell>
                      <Badge variant={t.is_active ? "default" : "secondary"}>
                        {t.is_active ? "active" : "inactive"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button asChild variant="ghost" size="sm">
                        <a
                          href={`https://gen.nomadkaraoke.com/en/app?preview_tenant=${t.id}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open <ExternalLink className="h-3 w-3 ml-1" />
                        </a>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Create tenant</DialogTitle>
            <DialogDescription>
              Provisions a branded portal with a locked theme. Jobs are private and delivered to
              Dropbox (or download only). You can drive it immediately via the preview link — no DNS.
            </DialogDescription>
          </DialogHeader>

          {created ? (
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">{created.tenant.name} is ready</CardTitle>
                  <CardDescription>
                    Open the preview link, switch to <strong>Bulk</strong>, and drop the album folder.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Input readOnly value={created.preview_url} className="font-mono text-xs" />
                    <Button variant="outline" size="icon" onClick={copyPreview}>
                      {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    </Button>
                  </div>
                  <div className="flex gap-2">
                    <Button asChild>
                      <a href={created.preview_url} target="_blank" rel="noreferrer">
                        Open bulk flow <ExternalLink className="h-4 w-4 ml-2" />
                      </a>
                    </Button>
                    <Button variant="outline" onClick={resetForm}>Create another</Button>
                    <Button variant="ghost" onClick={() => setDialogOpen(false)}>Done</Button>
                  </div>
                </CardContent>
              </Card>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>Name</Label>
                  <Input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Randy Vild" />
                </div>
                <div className="space-y-1">
                  <Label>Tenant ID</Label>
                  <Input value={form.tenant_id} onChange={(e) => set("tenant_id", e.target.value)} placeholder="randy-vild" className="font-mono" />
                </div>
              </div>
              <div className="space-y-1">
                <Label>Subdomain</Label>
                <Input value={form.subdomain} onChange={(e) => setForm((f) => ({ ...f, subdomain: e.target.value }))} placeholder="randy-vild.nomadkaraoke.com" className="font-mono text-sm" />
                <p className="text-xs text-muted-foreground">Only needed for a real client portal (DNS + Cloudflare). The preview link works without it.</p>
              </div>
              <div className="space-y-1">
                <Label>Allowed email domains <span className="text-muted-foreground">(optional)</span></Label>
                <Input value={form.allowed_email_domains} onChange={(e) => set("allowed_email_domains", e.target.value)} placeholder="client.com, label.com" />
                <p className="text-xs text-muted-foreground">Comma-separated. Leave blank for no restriction (you can always log in as admin).</p>
              </div>

              <div>
                <Label className="mb-2 block">Theme colours <span className="text-muted-foreground">(blank = Nomad default)</span></Label>
                <div className="grid grid-cols-2 gap-3">
                  <ColorField label="Artist" value={form.artist_color} onChange={(v) => set("artist_color", v)} placeholder="#ffdf6b" />
                  <ColorField label="Title" value={form.title_color} onChange={(v) => set("title_color", v)} placeholder="#ffffff" />
                  <ColorField label="Highlight (sung)" value={form.sung_lyrics_color} onChange={(v) => set("sung_lyrics_color", v)} placeholder="#7070f7" />
                  <ColorField label="Lyrics (unsung)" value={form.unsung_lyrics_color} onChange={(v) => set("unsung_lyrics_color", v)} placeholder="#ffffff" />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>Karaoke background <span className="text-muted-foreground">(optional)</span></Label>
                  <Input type="file" accept=".png,.jpg,.jpeg,.gif,.webp" onChange={(e) => setKaraokeBg(e.target.files?.[0] ?? null)} />
                </div>
                <div className="space-y-1">
                  <Label>Title-card background <span className="text-muted-foreground">(optional)</span></Label>
                  <Input type="file" accept=".png,.jpg,.jpeg,.gif,.webp" onChange={(e) => setIntroBg(e.target.files?.[0] ?? null)} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>Dropbox path <span className="text-muted-foreground">(optional)</span></Label>
                  <Input value={form.dropbox_path} onChange={(e) => set("dropbox_path", e.target.value)} placeholder="/MediaUnsynced/Karaoke/Tracks-RandyVild" className="font-mono text-sm" />
                  <p className="text-xs text-muted-foreground">Set = deliver to Dropbox. Blank = download only.</p>
                </div>
                <div className="space-y-1">
                  <Label>Brand prefix <span className="text-muted-foreground">(optional)</span></Label>
                  <Input value={form.brand_prefix} onChange={(e) => set("brand_prefix", e.target.value)} placeholder="RVILD" />
                </div>
              </div>

              <div className="space-y-1">
                <Label>Logo <span className="text-muted-foreground">(optional)</span></Label>
                <Input type="file" accept=".png,.jpg,.jpeg,.gif,.webp" onChange={(e) => setLogo(e.target.files?.[0] ?? null)} />
              </div>

              <DialogFooter>
                <Button variant="ghost" onClick={() => setDialogOpen(false)} disabled={submitting}>Cancel</Button>
                <Button onClick={handleSubmit} disabled={submitting}>
                  {submitting ? <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Creating…</> : "Create tenant"}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
