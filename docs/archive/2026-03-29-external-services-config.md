# External Services Configuration (DR Reference)

**Purpose:** If GCP or other services are terminated, this documents what needs to be re-created at the external service level. Actual secrets are in AWS Secrets Manager.

**Last verified:** 2026-03-29

## Cloudflare

- **Zone:** nomadkaraoke.com
- **DNS Records:**
  - `api` → CNAME → `ghs.googlehosted.com` (proxied: false)
  - `gen` → CNAME → Cloudflare Pages
  - `decide` → CNAME → GitHub Pages
  - `vocalstar` → CNAME → Cloudflare Pages (tenant portal)
  - `singa` → CNAME → Cloudflare Pages (tenant portal)
- **Pages Projects:**
  - `karaoke-gen` — gen.nomadkaraoke.com (consumer frontend)
  - `karaoke-gen-tenant` — vocalstar/singa subdomains (B2B tenant portal)
- **Workers:**
  - `karaoke-decide-api-proxy` — proxies decide.nomadkaraoke.com/api/* to Cloud Run

## Spotify Developer App

- **App name:** Nomad Karaoke Decide
- **Client ID:** stored in Secret Manager as `spotipy-client-id`
- **Client Secret:** stored in Secret Manager as `spotipy-client-secret`
- **Redirect URIs:**
  - `https://decide.nomadkaraoke.com/api/services/spotify/callback`
  - `http://localhost:8000/api/services/spotify/callback`

## Google (Flacfetch)

- **Account:** nomadflacfetch@gmail.com (no 2FA)
- **Used for:** YouTube cookies (refreshed every 8h), Spotify "Login with Google" (refreshed every 12h)
- **Managed by:** Flacfetch credential keeper systemd service on GCE VM

## Stripe

- **Webhook endpoint:** `https://api.nomadkaraoke.com/api/users/webhooks/stripe`
- **Events:** `checkout.session.completed`, `checkout.session.expired`, `payment_intent.payment_failed`
- **Keys:** `stripe-secret-key`, `stripe-webhook-secret` in Secret Manager

## SendGrid

- **Sender:** gen@nomadkaraoke.com
- **Used by:** karaoke-gen (job notifications, idle reminders), karaoke-decide (magic link auth)
- **Key:** `sendgrid-api-key` in Secret Manager

## AudioShake

- **Purpose:** Lyrics transcription API
- **Key:** `audioshake-api-key` in Secret Manager

## Genius

- **Purpose:** Reference lyrics lookup
- **Key:** `genius-api-key` in Secret Manager

## KaraokeNerds

- **Purpose:** Karaoke catalog API (daily sync)
- **Key:** `karaokenerds-api-key` in Secret Manager

## Dropbox

- **Purpose:** Karaoke file distribution (per-job upload)
- **Credentials:** `dropbox-oauth-credentials` in Secret Manager

## YouTube

- **Purpose:** Karaoke video upload + API quota tracking
- **Credentials:** `youtube-oauth-credentials`, `youtube-client-credentials`, `youtube-cookies` in Secret Manager

## Last.fm

- **Purpose:** User listening history for Decide recommendations
- **Keys:** `lastfm-api-key`, `lastfm-shared-secret` in Secret Manager

## Private Trackers (RED, OPS)

- **Purpose:** High-quality audio source downloads via Flacfetch
- **Keys:** `red-api-key`, `red-api-url`, `ops-api-key`, `ops-api-url` in Secret Manager
