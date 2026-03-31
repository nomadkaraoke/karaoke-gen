# Service Health Status Modal — Design Spec

## Overview

Replace the static footer version/status line with a clickable element that opens a modal showing detailed system status. Non-admin users see service health, versions, and deploy info. Admin users additionally see blue-green encoder deployment details. Each service links to the PR that produced its current version.

## Footer Changes

The existing footer line (`Frontend: v0.155.3 | Backend: v0.155.3 | ● Encoder: v0.155.3 | ...`) becomes a single clickable element. On hover: `cursor: pointer`, slight opacity change. No other visual changes to the footer layout.

Clicking anywhere on the version/status line opens the modal.

## Modal — Public View (everyone)

### Layout

- Standard modal: dark backdrop (click to dismiss), X close button, title "System Status"
- Responsive card grid: 2 columns on desktop, 1 column on mobile
- 5 service cards: **Frontend**, **Backend**, **Encoder**, **Flacfetch**, **Separator**

### Card Contents

Each card shows:

| Element | Example |
|---------|---------|
| Status indicator | Green dot (●) + "Healthy" or red dot (○) + "Offline" |
| Version | `v0.155.3` |
| Deploy time | "Deployed 2 hours ago" (relative) |
| Latest change | `#644 — fix blue-green deploy` (clickable link to GitHub PR) |

The "Latest change" line links to the GitHub PR that produced the currently deployed version. If unavailable, this line is omitted.

## Modal — Admin View (additional detail)

When the user has an admin token (stored in localStorage from login), the modal request includes it and the response includes extra data.

### Encoder Blue-Green Section

Inside the Encoder card, below the standard info, an expandable section labeled "Blue-Green Deployment" shows:

- **Two side-by-side mini-cards**: Primary (green-tinted border) and Secondary (muted/grey)
- Each mini-card shows:
  - VM name (e.g., `encoding-worker-b`)
  - Version (e.g., `v0.155.3`)
  - Role label: "PRIMARY" (green) or "SECONDARY (stopped)" (muted)
- **Deploy status line**: "Last swap: 2 hours ago" or "⚠ Deploy in progress..." (amber)
- **Active jobs**: count on primary, if > 0

### Admin Detail on Other Cards

For Backend, Flacfetch, and Separator cards, admin view additionally shows:
- Error message if status is offline (from the health check error field)

No extra admin detail needed for Frontend (it's a static site).

## New Backend Endpoint

### `GET /api/health/system-status`

Single endpoint that aggregates all service health data. Avoids the current pattern of 4 separate fetches.

**Authentication**: Optional `X-Admin-Token` header. If valid admin token, response includes `admin_details`. If omitted or invalid, those fields are absent.

**Response (public fields)**:

```json
{
  "services": {
    "frontend": {
      "status": "ok",
      "version": "0.155.3",
      "deployed_at": "2026-03-31T18:10:00Z",
      "commit_sha": "8e2a949a",
      "pr_number": 644,
      "pr_title": "fix: complete blue-green deploy test payload and status check"
    },
    "backend": {
      "status": "ok",
      "version": "0.155.3",
      "deployed_at": "2026-03-31T18:10:00Z",
      "commit_sha": "8e2a949a",
      "pr_number": 644,
      "pr_title": "fix: complete blue-green deploy test payload and status check"
    },
    "encoder": {
      "status": "ok",
      "version": "0.155.3",
      "deployed_at": "2026-03-31T18:10:00Z",
      "active_jobs": 0
    },
    "flacfetch": {
      "status": "ok",
      "version": "0.19.1"
    },
    "separator": {
      "status": "ok",
      "version": "0.4.3"
    }
  }
}
```

**Additional fields when admin token is valid**:

```json
{
  "services": {
    "encoder": {
      "admin_details": {
        "primary_vm": "encoding-worker-b",
        "primary_ip": "34.10.189.118",
        "primary_version": "0.155.3",
        "primary_deployed_at": "2026-03-31T18:10:00Z",
        "secondary_vm": "encoding-worker-a",
        "secondary_ip": "34.57.78.246",
        "secondary_version": "0.155.2",
        "secondary_deployed_at": "2026-03-29T14:00:00Z",
        "last_swap_at": "2026-03-31T18:10:00Z",
        "deploy_in_progress": false,
        "active_jobs": 0,
        "queue_length": 0
      }
    },
    "flacfetch": {
      "admin_details": {
        "error": null
      }
    },
    "separator": {
      "admin_details": {
        "error": null
      }
    }
  }
}
```

## Build Metadata — Git SHA and PR Info

Currently no git metadata is baked into builds. We need to add this.

### CI Changes (`.github/workflows/ci.yml`)

Add a step early in the deploy job that captures git info:

```yaml
- name: Capture build metadata
  id: build-meta
  run: |
    echo "sha=${GITHUB_SHA::8}" >> $GITHUB_OUTPUT
    # For merge commits, extract PR number from commit message
    PR_NUM=$(git log -1 --format=%s | grep -oP '#\K[0-9]+' | head -1)
    PR_TITLE=$(gh pr view "$PR_NUM" --json title -q .title 2>/dev/null || echo "")
    echo "pr_number=${PR_NUM}" >> $GITHUB_OUTPUT
    echo "pr_title=${PR_TITLE}" >> $GITHUB_OUTPUT
```

### Where metadata is stored

| Service | Storage mechanism |
|---------|------------------|
| **Frontend** | New env vars `NEXT_PUBLIC_COMMIT_SHA`, `NEXT_PUBLIC_PR_NUMBER`, `NEXT_PUBLIC_PR_TITLE`, `NEXT_PUBLIC_BUILD_TIME` set in CI before `npm run build`. Baked into the static bundle at build time via `next.config.mjs`. The `deployed_at` for frontend is the build timestamp (close enough to deploy time for Cloudflare Pages). |
| **Backend** | New env vars `COMMIT_SHA`, `PR_NUMBER`, `PR_TITLE` set on the Cloud Run service. Read at runtime in `backend/version.py`. |
| **Encoder** | Written to GCS version manifest alongside the existing `version.txt`. The GCE worker reads it on startup and includes in `/health` response. |
| **Flacfetch / Separator** | These are separate repos with their own CI. PR info can be added later — for now, omit the "Latest change" line for these services. |

### GitHub PR Link Format

The frontend constructs the PR link as:
```
https://github.com/nomadkaraoke/karaoke-gen/pull/{pr_number}
```

Displayed as: `#{pr_number} — {pr_title}` (truncated to ~60 chars if needed).

## Frontend Components

### New files
- `frontend/components/system-status-modal.tsx` — the modal component

### Modified files
- `frontend/components/version-footer.tsx` — make status line clickable, add modal toggle state
- `frontend/lib/api.ts` — add `SystemStatus` types and `getSystemStatus()` method
- `frontend/next.config.mjs` — pass through new `NEXT_PUBLIC_COMMIT_SHA`, `NEXT_PUBLIC_PR_NUMBER`, `NEXT_PUBLIC_PR_TITLE` env vars

### Modified backend files
- `backend/api/routes/health.py` — new `/api/health/system-status` endpoint
- `backend/version.py` — read `COMMIT_SHA`, `PR_NUMBER`, `PR_TITLE` env vars
- `.github/workflows/ci.yml` — capture build metadata, pass to builds/deploys

### Data Flow

1. User clicks footer → modal opens, state set to loading
2. Modal calls `GET /api/health/system-status` (with admin token header if available in localStorage)
3. Endpoint aggregates: existing health checks + Firestore blue-green config + build metadata env vars
4. Frontend renders cards from response
5. No auto-refresh — data is fresh on each open

## Styling

- Follow existing project design patterns (Tailwind, CSS variables for theming)
- Modal uses the same dark theme as the rest of the app
- Cards use `var(--card-border)`, `var(--card-bg)` etc. for consistency
- Green: `text-green-500` for healthy, red: `text-red-500` for offline
- Blue-green section uses amber accent (`text-amber-500`, `border-amber-500/30`) to distinguish it as an operational detail

## Out of Scope

- Auto-refresh / polling in the modal (can be added later)
- Flacfetch and Separator PR info (separate repos, separate CI)
- Historical deploy data or deploy log
- Notifications or alerts for status changes
