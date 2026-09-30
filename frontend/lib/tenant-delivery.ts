/** Admin tenant setup: Dropbox delivery defaults + validation. */

// Mirrors the existing tenants: "Vocal Star" → /MediaUnsynced/Karaoke/Tracks-VocalStar + VSTAR,
// "Singa" → Tracks-Singa + SINGA, "Randy Vild" → Tracks-RandyVild + RVILD.
const DROPBOX_TENANT_PARENT = "/MediaUnsynced/Karaoke"

export function suggestDelivery(name: string): { dropbox_path: string; brand_prefix: string } {
  const words = name.trim().split(/[^A-Za-z0-9]+/).filter(Boolean)
  if (!words.length) return { dropbox_path: "", brand_prefix: "" }
  const pascal = words.map((w) => w[0].toUpperCase() + w.slice(1)).join("")
  const initials = words.slice(0, -1).map((w) => w[0]).join("")
  const prefix = (initials + words[words.length - 1]).toUpperCase().replace(/^[0-9]+/, "").slice(0, 8)
  return { dropbox_path: `${DROPBOX_TENANT_PARENT}/Tracks-${pascal}`, brand_prefix: prefix }
}

/** Dropbox delivery needs both fields (the worker skips the upload otherwise). */
export function deliveryError(path: string, prefix: string): string | null {
  if (!!path.trim() !== !!prefix.trim()) {
    return "Set both a Dropbox path and a brand prefix (or leave both blank for download only)."
  }
  return null
}
