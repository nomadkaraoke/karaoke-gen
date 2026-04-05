# Affiliate QR Code Generator — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Frontend-only feature in karaoke-gen

## Overview

Add a QR code generator to the referral dashboard (`/app/referrals`) that lets referrers create customizable, branded QR codes for their referral link. Users can choose dot styles, corner styles, colors, and center logos, then download the result as PNG or SVG.

## User Flow

1. User navigates to `/app/referrals`
2. Clicks "QR Code" button next to the existing "Copy Link" button
3. Dialog opens with live QR code preview and style controls
4. User customizes appearance (styles, colors, logo)
5. Clicks "Download PNG" or "Download SVG"
6. Dialog closes (or stays open for further tweaks)
7. Style preferences are saved to localStorage automatically

## Technical Approach

### Library

**`qr-code-styling`** (npm) — client-side QR code generation with styling support.

Provides:
- 6 dot styles: square, rounded, dots, classy, classy-rounded, extra-rounded
- 3 corner square styles: square, dot, extra-rounded
- 3 corner dot styles: square, dot
- Foreground/background color control
- Center image support
- PNG/SVG export

No backend changes required.

### New Files

| File | Purpose |
|------|---------|
| `frontend/components/referrals/QRCodeDialog.tsx` | QR code generator dialog component |

### Modified Files

| File | Change |
|------|--------|
| `frontend/components/referrals/ReferralDashboard.tsx` | Add "QR Code" button next to "Copy Link" |
| `frontend/messages/en.json` | Add i18n strings under `referrals` namespace |
| `frontend/package.json` | Add `qr-code-styling` dependency |

### No New Assets Needed

Center logo options will use:
- `/nomad-karaoke-logo.svg` (already in `public/`)
- Lucide icons rendered to data URLs at build time: `Mic`, `Music`

## Component Design

### `QRCodeDialog`

**Props:**
```typescript
interface QRCodeDialogProps {
  referralUrl: string;  // e.g. "https://nomadkaraoke.com/r/abc123"
  open: boolean;
  onOpenChange: (open: boolean) => void;
}
```

**Internal State:**
```typescript
interface QRStylePrefs {
  dotStyle: 'square' | 'rounded' | 'dots' | 'classy' | 'classy-rounded' | 'extra-rounded';
  cornerSquareStyle: 'square' | 'dot' | 'extra-rounded';
  cornerDotStyle: 'square' | 'dot';
  fgColor: string;       // hex, default "#000000"
  bgColor: string;       // hex, default "#ffffff"
  logo: 'none' | 'nomad' | 'mic' | 'music';
}
```

**localStorage key:** `nk-qr-style-prefs`

### Dialog Layout

Uses existing `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle` from `@/components/ui/dialog`.

The dialog uses `max-w-2xl` (wider than the default `max-w-lg`) to accommodate two columns.

```
+--------------------------------------------------+
| QR Code Generator                            [X] |
+--------------------------------------------------+
|                    |                              |
|   [QR Preview]     |  Dot Style                  |
|   250x250          |  [6 clickable thumbnails]    |
|                    |                              |
|                    |  Corner Frame                |
|                    |  [3 clickable thumbnails]    |
|                    |                              |
|                    |  Corner Dot                  |
|                    |  [2 clickable options]       |
|                    |                              |
|                    |  Colors                      |
|                    |  FG: [picker] BG: [picker]   |
|                    |                              |
|                    |  Center Logo                 |
|                    |  (o) None (o) NK (o) Mic ... |
|                    |                              |
+--------------------------------------------------+
|              [Download PNG] [Download SVG]        |
+--------------------------------------------------+
```

**Responsive (<768px):** Single column — preview on top, controls below, scrollable.

### Style Picker UX

Each style option is a small clickable tile showing a mini QR code preview or icon representation. The selected tile has a ring/border highlight (matching the app's `--primary` color).

For dot styles and corner styles, render small labeled icons rather than live mini QR codes (which would be expensive). Labels like "Rounded", "Dots", "Classy" with a simple shape icon.

### Color Pickers

Use native `<input type="color">` for simplicity. Styled with a small swatch preview next to each picker. Two pickers: foreground and background.

### Center Logo Options

Radio group with 4 options:
- **None** — no center image
- **Nomad Karaoke** — `/nomad-karaoke-logo.svg`
- **Microphone** — Lucide `Mic` icon rendered as SVG data URL
- **Music** — Lucide `Music` icon rendered as SVG data URL

Logo is rendered at ~20% of QR code size with a white background margin for scannability.

### Download

- `qr-code-styling` provides `.download({ name, extension })` method
- PNG button: downloads as `referral-qr.png`
- SVG button: downloads as `referral-qr.svg`

## i18n Strings

Added to the `referrals` namespace in `en.json`:

```json
{
  "qrCode": "QR Code",
  "qrTitle": "QR Code Generator",
  "qrDotStyle": "Dot Style",
  "qrCornerFrame": "Corner Frame",
  "qrCornerDot": "Corner Dot",
  "qrColors": "Colors",
  "qrForeground": "Foreground",
  "qrBackground": "Background",
  "qrLogo": "Center Logo",
  "qrLogoNone": "None",
  "qrLogoNomad": "Nomad Karaoke",
  "qrLogoMic": "Microphone",
  "qrLogoMusic": "Music",
  "qrDownloadPng": "Download PNG",
  "qrDownloadSvg": "Download SVG",
  "qrStyleSquare": "Square",
  "qrStyleRounded": "Rounded",
  "qrStyleDots": "Dots",
  "qrStyleClassy": "Classy",
  "qrStyleClassyRounded": "Classy Rounded",
  "qrStyleExtraRounded": "Extra Rounded"
}
```

## Persistence

On every style change, debounce (300ms) and save the `QRStylePrefs` object to `localStorage` under key `nk-qr-style-prefs`. On dialog open, read from localStorage and apply. If no saved prefs, use defaults (square dots, square corners, black on white, no logo).

## Testing

- **Unit tests:** Verify localStorage persistence logic (save/load/defaults)
- **Component test:** Verify dialog renders, style options are clickable, download buttons present
- **E2E test:** Open referral dashboard, click QR Code button, verify dialog opens with preview, change a style, download PNG

## Out of Scope

- Custom image upload for center logo
- Gradient colors (could add later)
- Saving QR style to backend/Firestore
- Sharing QR code directly (social media, etc.)
- QR code analytics (scan tracking)
