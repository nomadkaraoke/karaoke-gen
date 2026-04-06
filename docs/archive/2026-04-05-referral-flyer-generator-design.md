# Referral Marketing Flyer Generator — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Backend endpoint + frontend integration in karaoke-gen

## Overview

Add a "Generate Flyer" feature to the QR Code dialog that produces a professional print-ready PDF marketing flyer personalized with the referrer's code, discount, and styled QR code. Uses the existing flyer templates from kjbox (light + dark variants), rendered server-side with headless Chromium.

## User Flow

1. User opens QR Code dialog from referral dashboard
2. Customizes QR code style (dot style, colors, logo — already implemented)
3. Picks flyer theme: Light or Dark
4. Clicks "Generate Flyer"
5. Frontend generates QR as base64 PNG data URL using current style prefs
6. Frontend POSTs to backend with theme + QR data URL
7. Backend injects referral code + QR + discount into HTML template
8. Backend renders PDF with headless Chromium (same flags as kjbox)
9. PDF returned as download: `nomad-karaoke-referral-flyer.pdf`

## Backend

### New Endpoint

`POST /api/referrals/me/flyer`

**Auth:** Requires logged-in user (same as other `/me` endpoints).

**Request body:**
```json
{
  "theme": "light" | "dark",
  "qr_data_url": "data:image/png;base64,..."
}
```

**Logic:**
1. Look up user's referral link (code, discount_percent)
2. Read HTML template for the selected theme
3. Replace placeholders: `{{REFERRAL_CODE}}`, `{{QR_DATA_URL}}`, `{{DISCOUNT_PERCENT}}`
4. Write substituted HTML to temp file
5. Run headless Chromium to generate PDF
6. Return PDF as `application/pdf` with `Content-Disposition: attachment`
7. Clean up temp files

**Chromium command:**
```bash
chromium --headless --print-to-pdf=OUTPUT.pdf --no-margins --print-background \
  --no-pdf-header-footer --virtual-time-budget=5000 \
  --paper-width=8.5 --paper-height=11 INPUT.html
```

**Error handling:**
- Missing referral link → 404
- Chromium render failure → 500 with error message
- QR data URL too large (>500KB) → 400 validation error

### Template Storage

Two HTML files in `backend/templates/printables/`:
- `website-referral-flyer.html` (light theme)
- `website-referral-flyer-dark.html` (dark theme)

Copied from `kjbox/printables/` with three changes:
1. Replace `ANGEL` referral code with `{{REFERRAL_CODE}}`
2. Replace `src="qr-code-angel.svg"` with `src="{{QR_DATA_URL}}"`
3. Replace hardcoded "10% off" CTA text with `{{DISCOUNT_PERCENT}}% off`

### Flyer Service

New file `backend/services/flyer_service.py`:
- `generate_flyer_pdf(referral_code, discount_percent, theme, qr_data_url) -> bytes`
- Handles template loading, placeholder substitution, temp file management, Chromium invocation
- Returns raw PDF bytes

### Dockerfile Change

Add `chromium` to apt dependencies in `Dockerfile.base`:
```dockerfile
RUN apt-get update && apt-get install -y chromium && rm -rf /var/lib/apt/lists/*
```

Also need to find the Chromium binary path (typically `/usr/bin/chromium` on Debian).

## Frontend

### QRCodeDialog Changes

Add to the dialog footer (between the controls and download buttons):
- **Theme toggle:** Light / Dark radio buttons or segmented control
- **"Generate Flyer" button:** Secondary button alongside existing download buttons

**On click:**
1. Get QR as raw data: `qrRef.current.getRawData('png')` → Blob
2. Convert Blob to base64 data URL
3. POST to `/api/referrals/me/flyer` with `{ theme, qr_data_url }`
4. Download PDF from response blob
5. Show loading state while generating (flyer takes a few seconds)

### API Client

Add to `frontend/lib/api.ts`:
```typescript
generateFlyer(theme: 'light' | 'dark', qrDataUrl: string): Promise<Blob>
```

### i18n Strings

Add to `referrals` namespace:
```json
"flyerTheme": "Flyer Theme",
"flyerThemeLight": "Light",
"flyerThemeDark": "Dark",
"flyerGenerate": "Generate Flyer",
"flyerGenerating": "Generating..."
```

## Verification Strategy

### Phase 1: Local Chrome verification (before any backend changes)
- Create templatized HTML file with placeholders
- Write a Python script that substitutes values and runs local Chrome headless
- Verify PDF output looks correct

### Phase 2: Docker verification (before merge)
- Build the Docker base image locally with Chromium added
- Run the container and execute the same Chromium command inside it
- Verify PDF renders correctly with container's fonts + Chromium
- This catches missing deps, font issues, sandbox problems, etc.

### Phase 3: Standard CI + deploy
- Only after local Docker verification passes

## Testing

- **Unit tests:** Flyer service template substitution logic, input validation
- **Integration test:** Full endpoint test with mocked Chromium (verify correct flags passed)
- **Local Docker test:** Manual verification of PDF rendering in container environment

## Out of Scope

- Custom flyer text/messaging (uses fixed marketing copy)
- Additional flyer layouts beyond light/dark
- Batch flyer generation
- Flyer analytics
