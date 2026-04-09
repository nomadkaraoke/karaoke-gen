# Referral Flyer Customisation — Design Spec

**Date:** 2026-04-09
**Branch:** `feat/sess-20260409-1813-referral-flyer-customisation`
**Status:** Design approved, ready for implementation planning

## Overview

Expand the existing QR Code Generator dialog into a tabbed "Referral Tools" dialog with two tabs: **QR Code** and **Printable Flyer**. The Flyer tab provides a live preview with extensive customisation options and client-side export (PDF, HTML, PNG). This replaces the current server-side flyer generation with a fully client-side approach.

## Goals

1. Live flyer preview with instant feedback on customisation changes
2. Configurable colors, text, section visibility, branding, and print layout
3. Client-side export to PDF (primary), HTML, and PNG — no server round-trip
4. Preset themes (Light, Dark) with ability to save/load custom presets
5. Multi-per-page PDF layout (1, 2, or 4 flyers per US Letter page)

## Architecture

### Approach: Client-Side Rendering

The flyer is rendered as a React component in the dialog preview. Export uses `html2canvas` + `jsPDF` for PDF/PNG, and DOM serialization for HTML. This gives:
- Instant preview of all customisation changes
- Export to PDF, HTML, and PNG without backend involvement
- No backend changes required — existing server-side endpoints remain but are unused

### Component Structure

```
frontend/components/referrals/
├── ReferralToolsDialog.tsx      # Tabbed shell (QR Code | Printable Flyer)
├── QRCodeTab.tsx                # Extracted from current QRCodeDialog (preview + controls)
├── FlyerTab.tsx                 # Flyer preview + controls container
├── FlyerPreview.tsx             # Flyer rendered as React component (live preview)
├── FlyerControls.tsx            # Right-side customisation panel
├── FlyerExport.tsx              # PDF/HTML/PNG export logic
└── flyer-presets.ts             # Built-in presets + localStorage save/load helpers
```

### State Flow

- `ReferralToolsDialog` owns active tab state and QR style prefs (both tabs need them)
- `QRCodeTab` receives QR prefs + updater — works exactly as current `QRCodeDialog`
- `FlyerTab` receives QR prefs (read-only, for embedding styled QR in flyer) + owns its own flyer config state
- Flyer config persisted to localStorage under `nk-flyer-prefs` (same debounced pattern as QR prefs)
- A small "Edit QR Style" link on the Flyer tab (near the QR in the preview) switches to the QR tab

## Flyer Config Model

```typescript
interface FlyerConfig {
  // Colors
  bgColor: string;              // default: '#faf8f5'
  headlineGradient: string[];   // default: ['#c45aff', '#ff7acc', '#ff5a8a']
  headlineSubColor: string;     // default: '#1a1a1a'
  textColor: string;            // default: '#1a1a1a'
  subtextColor: string;         // default: '#666'
  accentColor: string;          // step numbers, divider

  // Text overrides (null = use default)
  headlineMain: string | null;
  headlineSub: string | null;
  subtitle: string | null;
  ctaLabel: string | null;
  ctaNote: string | null;
  steps: Array<{ title: string | null; desc: string | null }>;

  // Section visibility
  showSubtitle: boolean;
  showSteps: boolean;
  showDivider: boolean;
  showBottomFeatures: boolean;
  showBottomTagline: boolean;

  // Custom branding
  customLogoUrl: string | null;   // data URL from file upload

  // Print layout
  perPage: 1 | 2 | 4;
  marginMm: number;              // page margin in mm, default 15
}
```

## Built-in Presets

**Light:** Current light template defaults — cream background (`#faf8f5`), dark text, pink/purple accent gradient.

**Dark:** Current dark template defaults — dark background, light text, pink/purple accent gradient.

## Custom Presets

- Stored in localStorage under `nk-flyer-custom-presets` as `Array<{ name: string; config: FlyerConfig }>`
- "Save as Preset" button in controls — prompts for a name inline
- "Load Preset" dropdown shows built-in + custom presets
- "Delete" option on custom presets only
- Editing any value after loading a preset shows dropdown as "Custom" / unsaved state

## Flyer Preview

`FlyerPreview.tsx` is a React port of the existing `backend/templates/printables/website-referral-flyer.html`.

- Rendered at fixed internal size matching US Letter proportions (8.5" x 11")
- CSS `transform: scale()` fits it into the dialog preview area
- All config changes reflect instantly via React re-rendering
- QR code rendered via `qr-code-styling` instance using the current QR prefs from the QR tab
- Custom logo shown to the right of the Nomad Karaoke logo in the header

**Multi-page preview:** When `perPage` is 2 or 4, the preview shows the page layout with the flyer duplicated and scaled into a grid (1x2 or 2x2) with configured margins. Shows exactly what the printed page will look like.

## Flyer Controls Panel

`FlyerControls.tsx` — right side, scrollable, organized in collapsible sections:

### Preset Selector (top, always visible)
- Dropdown: Light, Dark, custom presets
- "Save as Preset" button

### Colors
- Background — color picker
- Headline gradient — 3 color pickers (start, mid, end)
- Headline sub — color picker
- Text — color picker
- Subtext — color picker
- Accent — color picker
- "Reset Colors" button

### Text Overrides
- Each field shows default as placeholder; empty = use default
- Headline main, Headline sub, Subtitle, CTA label, CTA note
- Steps 1-4: title + description each, collapsible per step

### Sections
- Toggle switches: Subtitle, How-It-Works Steps, Divider, Bottom Features, Bottom Tagline

### Branding
- File upload (png, jpg, svg)
- Thumbnail preview + "Remove" button

### Print Layout
- Per page: segmented button (1 / 2 / 4)
- Page margins: slider (0-30mm, default 15mm)

## Export

### Formats
- **PDF** (primary) — `html2canvas` at 2x resolution + `jsPDF`. Multi-per-page: render once, place multiple times at scale with margins.
- **HTML** — serialize flyer DOM + inline styles, Google Fonts as links. Self-contained file.
- **PNG** — `html2canvas` at 2x, single flyer only (ignores per-page setting).

### UX
- Primary button: "Download PDF"
- Secondary buttons: "Download HTML", "Download PNG"
- Filename: `nomad-karaoke-flyer-{referralCode}.{ext}` (overridable via `flyerFilename` prop)

### New Dependencies
- `html2canvas` — DOM-to-canvas rasterization
- `jspdf` — PDF composition
- ~200KB combined, client-side only

## Migration & Integration

### Dialog Rename
- `QRCodeDialog` → `ReferralToolsDialog` (same props interface)
- `ReferralDashboard.tsx` — update import
- `admin/referrals/page.tsx` — update import; `onGenerateFlyer` prop becomes unused but kept for backwards compat

### Dialog Sizing
- Expand from `sm:max-w-2xl` to `sm:max-w-4xl`

### i18n
- New keys in `referrals` namespace for tab names, control labels, section headers, preset names, export buttons
- Run `translate.py --target all` for all 33 locales
- Admin pages are English-only, no concern

### Backend
- No changes. Existing flyer endpoints remain functional but unused by frontend.

## Testing

- Unit tests for preset save/load logic and config defaults (`flyer-presets.ts`)
- Unit tests for export functions with mocked html2canvas/jsPDF (`FlyerExport.tsx`)
- Update existing `QRCodeDialog.test.tsx` → `ReferralToolsDialog.test.tsx`
  - Tab switching
  - Flyer config persistence to localStorage
  - QR-to-flyer state flow (QR prefs reflected in flyer preview)
  - Section visibility toggles
  - Preset loading/saving

## Decisions Log

| Decision | Rationale |
|----------|-----------|
| Client-side rendering over server-side | Instant preview, export to HTML/PNG/PDF, no backend changes |
| Tabbed dialog over separate modals | Cohesive UX, natural QR→flyer flow |
| QR config shared via parent state | Single source of truth, "Edit QR Style" link for easy tab switch |
| localStorage for presets | Same pattern as existing QR prefs, no backend persistence needed |
| html2canvas + jsPDF over other libs | Well-maintained, reasonable bundle size, handles single-page flyer well |
| Keep backend endpoints | No breaking changes, can deprecate later |
