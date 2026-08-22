"use client"

import { useTranslations } from "next-intl"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import type { BulkSettings } from "@/lib/api"

interface BulkBatchSettingsProps {
  settings: BulkSettings
  onChange: (settings: BulkSettings) => void
  disabled?: boolean
}

function Row({ id, label, hint, checked, onChange, disabled }: {
  id: string; label: string; hint: string; checked: boolean
  onChange: (v: boolean) => void; disabled?: boolean
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-2">
      <div className="min-w-0">
        <Label htmlFor={id} className="text-sm" style={{ color: "var(--text)" }}>{label}</Label>
        <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{hint}</p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  )
}

export function BulkBatchSettings({ settings, onChange, disabled }: BulkBatchSettingsProps) {
  const t = useTranslations("bulk")
  const set = (patch: Partial<BulkSettings>) => onChange({ ...settings, ...patch })

  return (
    <div className="rounded-lg border p-3" style={{ borderColor: "var(--card-border)" }}>
      <p className="text-sm font-medium mb-1" style={{ color: "var(--text)" }}>{t("batchSettings")}</p>
      <Row
        id="bulk-auto-lossless"
        label={t("autoPickLossless")}
        hint={t("autoPickLosslessHint")}
        checked={settings.auto_select_if_lossless}
        onChange={(v) => set({ auto_select_if_lossless: v })}
        disabled={disabled}
      />
      <Row
        id="bulk-private"
        label={t("makePrivate")}
        hint={t("makePrivateHint")}
        checked={settings.is_private}
        onChange={(v) => set({ is_private: v })}
        disabled={disabled}
      />
      <Row
        id="bulk-skip-edit"
        label={t("skipAudioEdit")}
        hint={t("skipAudioEditHint")}
        checked={settings.skip_audio_edit}
        onChange={(v) => set({ skip_audio_edit: v })}
        disabled={disabled}
      />
      <Row
        id="bulk-skip-custom"
        label={t("skipCustomization")}
        hint={t("skipCustomizationHint")}
        checked={settings.skip_customization}
        onChange={(v) => set({ skip_customization: v })}
        disabled={disabled}
      />
    </div>
  )
}
