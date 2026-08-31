"use client"

import { useEffect, useState, useCallback } from "react"
import { adminApi, TenantSummary, TenantCreateResult, TenantDetail } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import {
  Building2, Plus, RefreshCw, Loader2, Copy, Check, ExternalLink, Settings2, Code2, WandSparkles,
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
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="font-mono" />
      </div>
    </div>
  )
}

// --- Full theme JSON editor -------------------------------------------------
function ThemeJsonEditor({
  value, onChange, error,
}: {
  value: string
  onChange: (v: string) => void
  error: string | null
}) {
  const format = () => {
    try {
      onChange(JSON.stringify(JSON.parse(value), null, 2))
    } catch {
      /* leave as-is; error surfaced below */
    }
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="flex items-center gap-1.5"><Code2 className="h-3.5 w-3.5" /> Theme JSON (full customisation)</Label>
        <Button type="button" variant="ghost" size="sm" onClick={format} disabled={!value.trim()}>Format</Button>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        className="w-full h-72 rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        placeholder='{ "intro": { ... }, "karaoke": { ... }, "end": { ... }, "cdg": { ... } }'
      />
      {error
        ? <p className="text-xs text-destructive">{error}</p>
        : <p className="text-xs text-muted-foreground">Edit any theme parameter (fonts, gradients, regions, CDG settings…). Sections: intro, karaoke, end, cdg.</p>}
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

function validateJson(text: string): string | null {
  if (!text.trim()) return null
  try { JSON.parse(text); return null } catch (e: any) { return e.message || "Invalid JSON" }
}

export default function AdminTenantsPage() {
  const { toast } = useToast()
  const [tenants, setTenants] = useState<TenantSummary[]>([])
  const [loading, setLoading] = useState(true)

  // --- Create state ---------------------------------------------------------
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [idEdited, setIdEdited] = useState(false)
  const [karaokeBg, setKaraokeBg] = useState<File | null>(null)
  const [introBg, setIntroBg] = useState<File | null>(null)
  const [logo, setLogo] = useState<File | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [createStyleText, setCreateStyleText] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [created, setCreated] = useState<TenantCreateResult | null>(null)
  const [copied, setCopied] = useState(false)

  // --- Manage state ---------------------------------------------------------
  const [manageOpen, setManageOpen] = useState(false)
  const [manageId, setManageId] = useState<string | null>(null)
  const [manageLoading, setManageLoading] = useState(false)
  const [detail, setDetail] = useState<TenantDetail | null>(null)
  const [mCfg, setMCfg] = useState({
    name: "", subdomain: "", tagline: "", allowed_email_domains: "",
    dropbox_path: "", brand_prefix: "", distribution_mode: "download_only", is_active: true,
  })
  const [styleText, setStyleText] = useState("")
  const [newAssets, setNewAssets] = useState<File[]>([])
  const [saving, setSaving] = useState(false)

  const styleError = validateJson(styleText)
  const createStyleError = validateJson(createStyleText)

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
    setShowAdvanced(false)
    setCreateStyleText("")
    setCreated(null)
  }

  const openCreate = () => { resetForm(); setDialogOpen(true) }

  const loadTemplate = async () => {
    try {
      const data = await adminApi.getThemeTemplate()
      setCreateStyleText(JSON.stringify(data.style_params, null, 2))
      setShowAdvanced(true)
    } catch (err: any) {
      toast({ title: "Failed to load template", description: err.message, variant: "destructive" })
    }
  }

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      toast({ title: "Name required", description: "Enter a tenant name.", variant: "destructive" })
      return
    }
    if (createStyleError) {
      toast({ title: "Invalid theme JSON", description: createStyleError, variant: "destructive" })
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
      if (createStyleText.trim()) fd.append("style_params", createStyleText)
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

  // --- Manage ---------------------------------------------------------------
  const openManage = async (tenantId: string) => {
    setManageId(tenantId)
    setManageOpen(true)
    setManageLoading(true)
    setNewAssets([])
    try {
      const d = await adminApi.getTenant(tenantId)
      setDetail(d)
      const t = d.tenant
      setMCfg({
        name: t.name || "",
        subdomain: t.subdomain || "",
        tagline: t.branding?.tagline || "",
        allowed_email_domains: (t.auth?.allowed_email_domains || []).join(", "),
        dropbox_path: t.defaults?.dropbox_path || "",
        brand_prefix: t.defaults?.brand_prefix || "",
        distribution_mode: t.defaults?.distribution_mode || "download_only",
        is_active: t.is_active !== false,
      })
      setStyleText(JSON.stringify(d.style_params, null, 2))
    } catch (err: any) {
      toast({ title: "Failed to load tenant", description: err.message, variant: "destructive" })
      setManageOpen(false)
    } finally {
      setManageLoading(false)
    }
  }

  const handleSave = async () => {
    if (!manageId) return
    if (styleError) {
      toast({ title: "Invalid theme JSON", description: styleError, variant: "destructive" })
      return
    }
    setSaving(true)
    try {
      const domains = mCfg.allowed_email_domains.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
      const configUpdate: Record<string, any> = {
        name: mCfg.name.trim(),
        subdomain: mCfg.subdomain.trim(),
        is_active: mCfg.is_active,
        branding: { tagline: mCfg.tagline.trim() || null },
        defaults: {
          dropbox_path: mCfg.dropbox_path.trim() || null,
          brand_prefix: mCfg.brand_prefix.trim() || null,
          distribution_mode: mCfg.distribution_mode,
        },
        features: { dropbox_upload: !!mCfg.dropbox_path.trim() },
        auth: { allowed_email_domains: domains, require_email_domain: domains.length > 0 },
      }
      const fd = new FormData()
      fd.append("config", JSON.stringify(configUpdate))
      if (styleText.trim()) fd.append("style_params", styleText)
      for (const f of newAssets) fd.append("assets", f)

      await adminApi.updateTenant(manageId, fd)
      toast({ title: "Saved", description: "Re-render jobs to apply the updated theme." })
      loadTenants()
      setNewAssets([])
    } catch (err: any) {
      toast({ title: "Failed to save", description: err.message || "Unknown error", variant: "destructive" })
    } finally {
      setSaving(false)
    }
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
              client-supplied instrumentals — then edit the theme and re-render as the client iterates.
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="icon" onClick={loadTenants} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
          <Button onClick={openCreate}><Plus className="h-4 w-4 mr-2" /> Create tenant</Button>
        </div>
      </div>

      <Card>
        <CardHeader><CardTitle>All tenants</CardTitle></CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
          ) : tenants.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">No tenants yet. Create one to get started.</p>
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
                      <Badge variant={t.is_active ? "default" : "secondary"}>{t.is_active ? "active" : "inactive"}</Badge>
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      <Button variant="ghost" size="sm" onClick={() => openManage(t.id)}>
                        <Settings2 className="h-3.5 w-3.5 mr-1" /> Manage
                      </Button>
                      <Button asChild variant="ghost" size="sm">
                        <a href={`https://gen.nomadkaraoke.com/en/app?preview_tenant=${t.id}`} target="_blank" rel="noreferrer">
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

      {/* Create dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Create tenant</DialogTitle>
            <DialogDescription>
              Provisions a branded portal with a locked theme. Jobs are private and delivered to
              Dropbox (or download only). Drive it immediately via the preview link — no DNS.
            </DialogDescription>
          </DialogHeader>

          {created ? (
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">{created.tenant.name} is ready</CardTitle>
                  <CardDescription>Open the preview link, switch to <strong>Bulk</strong>, and drop the album folder.</CardDescription>
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
                      <a href={created.preview_url} target="_blank" rel="noreferrer">Open bulk flow <ExternalLink className="h-4 w-4 ml-2" /></a>
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
                <p className="text-xs text-muted-foreground">Comma-separated. Blank = no restriction (you can always log in as admin).</p>
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

              {/* Advanced: full theme JSON */}
              <div className="rounded-md border border-dashed p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="flex items-center gap-1.5"><Code2 className="h-3.5 w-3.5" /> Advanced: full theme JSON <span className="text-muted-foreground">(optional)</span></Label>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" size="sm" onClick={loadTemplate}>
                      <WandSparkles className="h-3.5 w-3.5 mr-1" /> Load Nomad template
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setShowAdvanced((s) => !s)}>
                      {showAdvanced ? "Hide" : "Show"}
                    </Button>
                  </div>
                </div>
                {showAdvanced && (
                  <>
                    <ThemeJsonEditor value={createStyleText} onChange={setCreateStyleText} error={createStyleError} />
                    <p className="text-xs text-muted-foreground">If provided, this full theme JSON is authoritative — colours above are ignored.</p>
                  </>
                )}
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

      {/* Manage dialog */}
      <Dialog open={manageOpen} onOpenChange={setManageOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Manage {manageId}</DialogTitle>
            <DialogDescription>
              Edit the tenant config and its full theme. Uploading a file named like an existing
              asset replaces it. Save, then re-render jobs to apply.
            </DialogDescription>
          </DialogHeader>

          {manageLoading || !detail ? (
            <div className="space-y-2 py-6">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>Name</Label>
                  <Input value={mCfg.name} onChange={(e) => setMCfg((c) => ({ ...c, name: e.target.value }))} />
                </div>
                <div className="space-y-1">
                  <Label>Subdomain</Label>
                  <Input value={mCfg.subdomain} onChange={(e) => setMCfg((c) => ({ ...c, subdomain: e.target.value }))} className="font-mono text-sm" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>Dropbox path</Label>
                  <Input value={mCfg.dropbox_path} onChange={(e) => setMCfg((c) => ({ ...c, dropbox_path: e.target.value }))} className="font-mono text-sm" placeholder="download only if blank" />
                </div>
                <div className="space-y-1">
                  <Label>Brand prefix</Label>
                  <Input value={mCfg.brand_prefix} onChange={(e) => setMCfg((c) => ({ ...c, brand_prefix: e.target.value }))} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>Allowed email domains</Label>
                  <Input value={mCfg.allowed_email_domains} onChange={(e) => setMCfg((c) => ({ ...c, allowed_email_domains: e.target.value }))} placeholder="client.com, label.com" />
                </div>
                <div className="space-y-1">
                  <Label>Tagline</Label>
                  <Input value={mCfg.tagline} onChange={(e) => setMCfg((c) => ({ ...c, tagline: e.target.value }))} />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={mCfg.is_active} onCheckedChange={(v) => setMCfg((c) => ({ ...c, is_active: v }))} id="active-switch" />
                <Label htmlFor="active-switch">Active</Label>
              </div>

              <ThemeJsonEditor value={styleText} onChange={setStyleText} error={styleError} />

              <div className="space-y-1">
                <Label>Current theme assets</Label>
                <div className="flex flex-wrap gap-1.5">
                  {detail.assets.length === 0
                    ? <span className="text-xs text-muted-foreground">none</span>
                    : detail.assets.map((a) => <Badge key={a} variant="secondary" className="font-mono text-[11px]">{a}</Badge>)}
                </div>
              </div>
              <div className="space-y-1">
                <Label>Add / replace assets <span className="text-muted-foreground">(image or font; filename becomes the asset name)</span></Label>
                <Input type="file" multiple accept=".png,.jpg,.jpeg,.gif,.webp,.ttf,.otf,.woff,.woff2" onChange={(e) => setNewAssets(Array.from(e.target.files ?? []))} />
                {newAssets.length > 0 && (
                  <p className="text-xs text-muted-foreground">Uploading: {newAssets.map((f) => f.name).join(", ")}</p>
                )}
              </div>

              <DialogFooter className="flex items-center justify-between sm:justify-between">
                <Button asChild variant="ghost">
                  <a href={detail.preview_url} target="_blank" rel="noreferrer">Open preview <ExternalLink className="h-4 w-4 ml-2" /></a>
                </Button>
                <div className="flex gap-2">
                  <Button variant="ghost" onClick={() => setManageOpen(false)} disabled={saving}>Close</Button>
                  <Button onClick={handleSave} disabled={saving || !!styleError}>
                    {saving ? <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Saving…</> : "Save changes"}
                  </Button>
                </div>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
