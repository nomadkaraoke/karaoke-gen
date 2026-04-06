"use client"

import { useEffect, useState, useCallback } from "react"
import { adminApi } from "@/lib/api"
import type { ReferralLink } from "@/lib/types"
import { StatsCard, StatsGrid } from "@/components/admin/stats-card"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogDescription,
} from "@/components/ui/dialog"
import {
  Share2, Link as LinkIcon, Users, MousePointerClick, DollarSign,
  Plus, Copy, Check, RefreshCw, Loader2, ToggleLeft, ToggleRight, Pencil, FileText,
} from "lucide-react"

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

// Extend ReferralLink with owner_email from admin endpoint
interface AdminReferralLink extends ReferralLink {
  owner_email?: string;
}

export default function ReferralsPage() {
  const [links, setLinks] = useState<AdminReferralLink[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [copiedCode, setCopiedCode] = useState<string | null>(null)
  const [togglingCode, setTogglingCode] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [editLink, setEditLink] = useState<AdminReferralLink | null>(null)
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [flyerGenerating, setFlyerGenerating] = useState<string | null>(null)

  // Edit form state
  const [editDisplayName, setEditDisplayName] = useState("")
  const [editMessage, setEditMessage] = useState("")
  const [editDiscount, setEditDiscount] = useState("10")
  const [editKickback, setEditKickback] = useState("20")
  const [editDiscountDays, setEditDiscountDays] = useState("30")
  const [editEarningDays, setEditEarningDays] = useState("365")

  // Create form state
  const [formCode, setFormCode] = useState("")
  const [formEmail, setFormEmail] = useState("")
  const [formDisplayName, setFormDisplayName] = useState("")
  const [formMessage, setFormMessage] = useState("")
  const [formDiscount, setFormDiscount] = useState("10")
  const [formKickback, setFormKickback] = useState("20")
  const [formDiscountDays, setFormDiscountDays] = useState("30")
  const [formEarningDays, setFormEarningDays] = useState("365")

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await adminApi.listReferralLinks({ limit: 200 })
      setLinks(result.links as AdminReferralLink[])
    } catch (err) {
      console.error("Failed to load referral links:", err)
      setError(err instanceof Error ? err.message : "Failed to load referral links")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // Aggregate stats
  const totalLinks = links.length
  const totalClicks = links.reduce((sum, l) => sum + (l.stats?.clicks ?? 0), 0)
  const totalSignups = links.reduce((sum, l) => sum + (l.stats?.signups ?? 0), 0)
  const totalEarned = links.reduce((sum, l) => sum + (l.stats?.total_earned_cents ?? 0), 0)

  const handleCopy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(`https://nomadkaraoke.com/r/${code}`)
      setCopiedCode(code)
      setTimeout(() => setCopiedCode(null), 2000)
    } catch {
      // Fallback for non-secure contexts
    }
  }

  const handleToggle = async (link: AdminReferralLink) => {
    setTogglingCode(link.code)
    try {
      await adminApi.updateReferralLink(link.code, { enabled: !link.enabled })
      await loadData()
    } catch (err) {
      console.error("Failed to toggle link:", err)
    } finally {
      setTogglingCode(null)
    }
  }

  const handleGenerateFlyer = async (code: string, theme: 'light' | 'dark') => {
    setFlyerGenerating(code)
    try {
      // Generate a standard black QR code for this referral link
      const QRCodeStyling = (await import('qr-code-styling')).default
      const qr = new QRCodeStyling({
        width: 250, height: 250,
        data: `https://nomadkaraoke.com/r/${code}`,
        dotsOptions: { type: 'square', color: '#000000' },
        cornersSquareOptions: { type: 'square', color: '#000000' },
        cornersDotOptions: { type: 'square', color: '#000000' },
        backgroundOptions: { color: '#ffffff' },
      })
      const blob = await qr.getRawData('svg')
      if (!blob) throw new Error('Failed to generate QR')
      const qrDataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onloadend = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('Failed to read QR'))
        reader.readAsDataURL(blob)
      })
      const pdfBlob = await adminApi.generateFlyer(code, theme, qrDataUrl)
      const url = URL.createObjectURL(pdfBlob)
      const a = document.createElement('a')
      a.href = url
      a.download = `nomad-karaoke-flyer-${code}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('Failed to generate flyer:', err)
      alert(`Failed to generate flyer: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setFlyerGenerating(null)
    }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    setCreateError(null)
    try {
      await adminApi.createVanityLink({
        vanity_code: formCode,
        owner_email: formEmail,
        display_name: formDisplayName || undefined,
        custom_message: formMessage || undefined,
        discount_percent: Number(formDiscount) || 10,
        kickback_percent: Number(formKickback) || 20,
        discount_duration_days: Number(formDiscountDays) || 30,
        earning_duration_days: Number(formEarningDays) || 365,
      })
      setCreateOpen(false)
      resetForm()
      await loadData()
    } catch (err) {
      console.error("Failed to create vanity link:", err)
      setCreateError(err instanceof Error ? err.message : "Failed to create link")
    } finally {
      setCreating(false)
    }
  }

  const openEdit = (link: AdminReferralLink) => {
    setEditLink(link)
    setEditDisplayName(link.display_name || "")
    setEditMessage(link.custom_message || "")
    setEditDiscount(String(link.discount_percent))
    setEditKickback(String(link.kickback_percent))
    setEditDiscountDays(String(link.discount_duration_days))
    setEditEarningDays(String(link.earning_duration_days))
    setEditError(null)
    setEditOpen(true)
  }

  const handleEdit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editLink) return
    setSaving(true)
    setEditError(null)
    try {
      await adminApi.updateReferralLink(editLink.code, {
        display_name: editDisplayName || null,
        custom_message: editMessage || null,
        discount_percent: Number(editDiscount) || 10,
        kickback_percent: Number(editKickback) || 20,
        discount_duration_days: Number(editDiscountDays) || 30,
        earning_duration_days: Number(editEarningDays) || 365,
      })
      setEditOpen(false)
      setEditLink(null)
      await loadData()
    } catch (err) {
      console.error("Failed to update link:", err)
      setEditError(err instanceof Error ? err.message : "Failed to update link")
    } finally {
      setSaving(false)
    }
  }

  const resetForm = () => {
    setFormCode("")
    setFormEmail("")
    setFormDisplayName("")
    setFormMessage("")
    setFormDiscount("10")
    setFormKickback("20")
    setFormDiscountDays("30")
    setFormEarningDays("365")
    setCreateError(null)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Referrals</h1>
          <p className="text-muted-foreground">Manage referral links, track performance, and create vanity codes</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Dialog open={createOpen} onOpenChange={(open) => { setCreateOpen(open); if (!open) resetForm() }}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="w-4 h-4 mr-2" />
                Create Vanity Link
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Create Vanity Link</DialogTitle>
                <DialogDescription>
                  Create a custom referral link with a memorable code.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="code">Code *</Label>
                  <Input
                    id="code"
                    placeholder="e.g. john, divebar"
                    value={formCode}
                    onChange={(e) => setFormCode(e.target.value)}
                    required
                  />
                  <p className="text-xs text-muted-foreground">
                    URL will be: nomadkaraoke.com/r/{formCode || "..."}
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Owner Email *</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="owner@example.com"
                    value={formEmail}
                    onChange={(e) => setFormEmail(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="displayName">Display Name</Label>
                  <Input
                    id="displayName"
                    placeholder="e.g. John's Karaoke"
                    value={formDisplayName}
                    onChange={(e) => setFormDisplayName(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="message">Custom Message</Label>
                  <Input
                    id="message"
                    placeholder="Welcome message for referred users"
                    value={formMessage}
                    onChange={(e) => setFormMessage(e.target.value)}
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="discount">Discount %</Label>
                    <Input
                      id="discount"
                      type="number"
                      min="0"
                      max="100"
                      value={formDiscount}
                      onChange={(e) => setFormDiscount(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="kickback">Kickback %</Label>
                    <Input
                      id="kickback"
                      type="number"
                      min="0"
                      max="100"
                      value={formKickback}
                      onChange={(e) => setFormKickback(e.target.value)}
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="formDiscountDays">Discount Duration (days)</Label>
                    <Input
                      id="formDiscountDays"
                      type="number"
                      min="1"
                      value={formDiscountDays}
                      onChange={(e) => setFormDiscountDays(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">How long referred users get the discount</p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="formEarningDays">Earning Duration (days)</Label>
                    <Input
                      id="formEarningDays"
                      type="number"
                      min="1"
                      value={formEarningDays}
                      onChange={(e) => setFormEarningDays(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">How long referrer earns from purchases</p>
                  </div>
                </div>
                {createError && (
                  <p className="text-sm text-destructive">{createError}</p>
                )}
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={creating || !formCode || !formEmail}>
                    {creating && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    Create Link
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {/* Stats */}
      <StatsGrid>
        <StatsCard
          title="Total Links"
          value={totalLinks}
          description={`${links.filter(l => l.enabled).length} active`}
          icon={LinkIcon}
          loading={loading}
        />
        <StatsCard
          title="Total Clicks"
          value={totalClicks.toLocaleString()}
          description="Across all links"
          icon={MousePointerClick}
          loading={loading}
        />
        <StatsCard
          title="Total Signups"
          value={totalSignups.toLocaleString()}
          description="From referrals"
          icon={Users}
          loading={loading}
        />
        <StatsCard
          title="Total Earned"
          value={formatCents(totalEarned)}
          description="Kickback earnings"
          icon={DollarSign}
          loading={loading}
          valueClassName="text-green-600 dark:text-green-400"
        />
      </StatsGrid>

      {/* Error State */}
      {error && (
        <Card>
          <CardContent className="py-8 text-center">
            <p className="text-destructive">{error}</p>
            <Button variant="outline" size="sm" className="mt-4" onClick={loadData}>
              Try Again
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Links Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Referral Links</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Code</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Display Name</TableHead>
                  <TableHead className="text-center">Rates</TableHead>
                  <TableHead className="text-right">Clicks</TableHead>
                  <TableHead className="text-right">Signups</TableHead>
                  <TableHead className="text-right">Purchases</TableHead>
                  <TableHead className="text-right">Earned</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-[100px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 10 }).map((_, j) => (
                        <TableCell key={j}><Skeleton className="h-4 w-16" /></TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : links.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={10} className="text-center py-8 text-muted-foreground">
                      No referral links found
                    </TableCell>
                  </TableRow>
                ) : (
                  links.map((link) => (
                    <TableRow key={link.code}>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <code className="text-sm font-mono bg-muted px-1.5 py-0.5 rounded">
                            {link.code}
                          </code>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            onClick={() => handleCopy(link.code)}
                            title="Copy referral URL"
                          >
                            {copiedCode === link.code ? (
                              <Check className="h-3 w-3 text-green-500" />
                            ) : (
                              <Copy className="h-3 w-3" />
                            )}
                          </Button>
                        </div>
                      </TableCell>
                      <TableCell className="text-sm max-w-[160px] truncate">
                        {link.owner_email || "-"}
                      </TableCell>
                      <TableCell className="text-sm max-w-[140px] truncate">
                        {link.display_name || "-"}
                      </TableCell>
                      <TableCell className="text-center text-sm">
                        <div>
                          <span>{link.discount_percent}% / {link.kickback_percent}%</span>
                          <span className="block text-xs text-muted-foreground">{link.discount_duration_days}d / {link.earning_duration_days}d</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right text-sm">{link.stats?.clicks ?? 0}</TableCell>
                      <TableCell className="text-right text-sm">{link.stats?.signups ?? 0}</TableCell>
                      <TableCell className="text-right text-sm">{link.stats?.purchases ?? 0}</TableCell>
                      <TableCell className="text-right text-sm font-medium">
                        {formatCents(link.stats?.total_earned_cents ?? 0)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={link.enabled ? "default" : "secondary"}>
                          {link.enabled ? "Enabled" : "Disabled"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => handleGenerateFlyer(link.code, 'light')}
                            disabled={flyerGenerating === link.code}
                            title="Generate flyer (light)"
                          >
                            {flyerGenerating === link.code ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <FileText className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => openEdit(link)}
                            title="Edit link"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => handleToggle(link)}
                            disabled={togglingCode === link.code}
                            title={link.enabled ? "Disable link" : "Enable link"}
                          >
                            {togglingCode === link.code ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : link.enabled ? (
                              <ToggleRight className="h-4 w-4 text-green-500" />
                            ) : (
                              <ToggleLeft className="h-4 w-4 text-muted-foreground" />
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
        </CardContent>
      </Card>

      {/* Edit Link Dialog */}
      <Dialog open={editOpen} onOpenChange={(open) => { setEditOpen(open); if (!open) setEditLink(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Referral Link: {editLink?.code}</DialogTitle>
            <DialogDescription>
              Update settings for this referral link. Owner: {editLink?.owner_email}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleEdit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="editDisplayName">Display Name</Label>
              <Input
                id="editDisplayName"
                placeholder="Shown on the interstitial page"
                value={editDisplayName}
                onChange={(e) => setEditDisplayName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="editMessage">Custom Message</Label>
              <Input
                id="editMessage"
                placeholder="Welcome message for referred users"
                value={editMessage}
                onChange={(e) => setEditMessage(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="editDiscount">Discount %</Label>
                <Input
                  id="editDiscount"
                  type="number"
                  min="0"
                  max="100"
                  value={editDiscount}
                  onChange={(e) => setEditDiscount(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="editKickback">Kickback %</Label>
                <Input
                  id="editKickback"
                  type="number"
                  min="0"
                  max="100"
                  value={editKickback}
                  onChange={(e) => setEditKickback(e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="editDiscountDays">Discount Duration (days)</Label>
                <Input
                  id="editDiscountDays"
                  type="number"
                  min="1"
                  value={editDiscountDays}
                  onChange={(e) => setEditDiscountDays(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">How long referred users get the discount</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="editEarningDays">Earning Duration (days)</Label>
                <Input
                  id="editEarningDays"
                  type="number"
                  min="1"
                  value={editEarningDays}
                  onChange={(e) => setEditEarningDays(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">How long referrer earns from purchases</p>
              </div>
            </div>
            {editError && (
              <p className="text-sm text-destructive">{editError}</p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setEditOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saving}>
                {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                Save Changes
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
