# External Services Configuration (DR Reference)

**Purpose:** If GCP or other services are terminated, this documents what needs to be re-created at the external service level. Live secret values are in GCP Secret Manager; an encrypted snapshot lives in `s3://nomadkaraoke-backup/secrets/` (decrypt with the private key from KeepassXC — see `docs/DISASTER-RECOVERY.md`).

**Last verified:** 2026-04-20

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
- **API Tokens (rotate during DR — owner: Andrew's Cloudflare account):**
  - Cloudflare Pages deploy token (CI deploys frontend) — referenced by GitHub Actions secret `CLOUDFLARE_API_TOKEN`
  - Account ID — GitHub Actions secret `CLOUDFLARE_ACCOUNT_ID`
  - DNS edit token (if managing DNS via IaC) — see `docs/archive/2026-04-04-cloudflare-iac-migration-plan.md`

## Spotify Developer App

- **App name:** Nomad Karaoke Decide
- **Client ID:** stored in Secret Manager as `spotipy-client-id`
- **Client Secret:** stored in Secret Manager as `spotipy-client-secret`
- **Redirect URIs:**
  - `https://decide.nomadkaraoke.com/api/services/spotify/callback`
  - `http://localhost:8000/api/services/spotify/callback`
- **DR action if domain changes:** update redirect URIs in Spotify Developer Dashboard.

## Google (Flacfetch)

- **Account:** nomadflacfetch@gmail.com (no 2FA)
- **Used for:** YouTube cookies (refreshed every 8h), Spotify "Login with Google" (refreshed every 12h)
- **Managed by:** Flacfetch credential keeper systemd service on GCE VM

## Stripe

- **Webhook endpoint:** `https://api.nomadkaraoke.com/api/users/webhooks/stripe`
- **Events:** `checkout.session.completed`, `checkout.session.expired`, `payment_intent.payment_failed`
- **Keys:** `stripe-secret-key`, `stripe-webhook-secret` in Secret Manager
- **DR action:** webhook endpoint URL must be re-registered if API hostname changes; Stripe regenerates the webhook signing secret on re-registration → update `stripe-webhook-secret` in Secret Manager.
- **Connect (referrals):** Stripe Connect platform settings — referral payouts via Standard accounts; see `docs/archive/2026-04-04-referral-system-design.md`.

## SendGrid

- **Sender:** gen@nomadkaraoke.com
- **Used by:** karaoke-gen (job notifications, idle reminders), karaoke-decide (magic link auth)
- **Key:** `sendgrid-api-key` in Secret Manager
- **DR action:** sender domain authentication (SPF/DKIM) is per-domain; if domain changes, re-verify in SendGrid → may take 24-48h for DNS to propagate.

## AudioShake

- **Purpose:** Lyrics transcription API
- **Key:** `audioshake-api-key` in Secret Manager
- **DR action:** if old GCP project was compromised, rotate the key in AudioShake console as a precaution.

## Genius

- **Purpose:** Reference lyrics lookup
- **Key:** `genius-api-key` in Secret Manager

## KaraokeNerds

- **Purpose:** Karaoke catalog API (daily sync)
- **Key:** `karaokenerds-api-key` in Secret Manager

## Dropbox

- **Purpose:** Karaoke file distribution (per-job upload)
- **Credentials:** `dropbox-oauth-credentials` in Secret Manager
- **DR action:** Dropbox app's redirect URI is locked to current API hostname; update in Dropbox App Console if it changes.

## YouTube

- **Purpose:** Karaoke video upload + API quota tracking
- **Credentials:** `youtube-oauth-credentials`, `youtube-client-credentials`, `youtube-cookies` in Secret Manager
- **DR action:** OAuth client redirect URIs and OAuth consent screen are project-scoped — must be re-created in the new GCP project's API Console; user re-consent will be required on first use.

## Last.fm

- **Purpose:** User listening history for Decide recommendations
- **Keys:** `lastfm-api-key`, `lastfm-shared-secret` in Secret Manager

## Private Trackers (RED, OPS)

- **Purpose:** High-quality audio source downloads via Flacfetch
- **Keys:** `red-api-key`, `red-api-url`, `ops-api-key`, `ops-api-url` in Secret Manager
- **DR action:** these keys are tied to user accounts on the trackers — no re-issuance needed unless old GCP exposure could have leaked them, in which case generate new keys in tracker user settings.

## Discord

Multiple webhook URLs power different alert channels — all in Secret Manager:

| Secret name | Purpose | Channel |
|---|---|---|
| `discord-alert-webhook` | Nightly backup status, error monitor digests, DR freshness | #ops-alerts |
| `discord-releases-webhook` | Deploy/release notifications | #releases |
| `discord-webhook-url` | Generic catch-all (legacy) | #ops-general |

**DR action:** if old GCP exposure could have leaked them, regenerate webhook URLs in Discord channel integration settings (right-click channel → Edit Channel → Integrations → Webhooks → reset URL). Webhook URLs themselves are the only credential — no API key.

## Pushbullet

- **Purpose:** Mobile push for kjbox / live event alerts
- **Key:** `pushbullet-api-key` in Secret Manager
- **DR action:** rotate in Pushbullet account settings if old GCP was compromised.

## Mattermost

- **Purpose:** Older notification path (may be deprecated — verify before relying on)
- **Token:** `mattermost-token` in Secret Manager

## Coinbase

- **Purpose:** Crypto payment option (referral system fiat off-ramp / experimental)
- **Keys:** `coinbase-api-key`, `coinbase-api-secret`, `coinbase-key-file` in Secret Manager
- **DR action:** Coinbase Commerce API key is tied to merchant account — rotate in Coinbase merchant settings if compromised; webhook URL needs re-registering if API hostname changes.

## Langfuse

- **Purpose:** LLM observability (Gemini call traces from translation pipeline + agent flows)
- **Keys:** `langfuse-public-key`, `langfuse-secret-key`, `langfuse-host` in Secret Manager
- **DR action:** project-scoped on langfuse.com — rotate keys in Langfuse project settings if compromised.

## Vertex AI / Gemini

- **Purpose:** i18n translation pipeline (Gemini 3.1 Pro), agent reasoning
- **Auth:** Application Default Credentials (no API key — uses service account on Cloud Run)
- **DR action:** must enable Vertex AI API in new GCP project (`gcloud services enable aiplatform.googleapis.com`); no key rotation needed; quota limits restart from zero.

## VerifyMail

- **Purpose:** Email validation (anti-abuse — checks disposable email domains)
- **Key:** `verifymail-api-key` in Secret Manager

## RapidAPI

- **Purpose:** Various third-party APIs accessed via RapidAPI gateway
- **Key:** `rapidapi-key` in Secret Manager
- **DR action:** rotate in RapidAPI dashboard.

## VAPID (Web Push)

- **Purpose:** Mobile push notifications via service worker
- **Keys:** `vapid-public-key`, `vapid-private-key` in Secret Manager
- **DR action:** if keys are rotated, all currently-subscribed mobile devices lose push and must re-subscribe. Avoid rotation unless compromised. Public key is also embedded in frontend build — rotation requires frontend redeploy.

## GitHub

- **Runner PAT:** `github-runner-pat` in Secret Manager — used by self-hosted runner manager to register/deregister GCE-hosted runners
- **Webhook secret:** `github-webhook-secret` in Secret Manager — verifies webhook deliveries to runner manager
- **DR action:** generate new PAT under nomadkaraoke org with `repo` + `workflow` scopes; rotate webhook secret in repo settings.

## Karaoke-Decide JWT

- **Purpose:** Internal JWT signing for decide → gen API calls
- **Key:** `karaoke-decide-jwt-secret` in Secret Manager
- **DR action:** rotation invalidates all in-flight tokens — coordinate decide + gen restart.

## E2E Testing

- **Bypass key:** `e2e-bypass-key` in Secret Manager — allows Playwright E2E tests to skip rate limits / payment
- **DR action:** rotate after recovery to ensure no leaked test bypass remains in old project's logs.

## Admin Tokens

- **Key:** `admin-tokens` in Secret Manager (comma-separated list of valid admin bearer tokens)
- **DR action:** generate fresh tokens; old tokens in scripts / shell history must be replaced.

## GitHub Actions Secrets (CI/CD)

These are not in GCP Secret Manager — they live in GitHub repo settings. Document the full list here:

| Secret | Used by | Notes |
|---|---|---|
| `GCP_PROJECT_ID` | All deploy workflows | Update on DR — points to new project ID |
| `GCP_SA_KEY` | All deploy workflows | New SA key per-project |
| `CLOUDFLARE_API_TOKEN` | Frontend deploy | Pages publish |
| `CLOUDFLARE_ACCOUNT_ID` | Frontend deploy | Pages publish |
| `AWS_BACKUP_READONLY_ACCESS_KEY_ID` | DR freshness monitor | Created during DR setup |
| `AWS_BACKUP_READONLY_SECRET_ACCESS_KEY` | DR freshness monitor | Created during DR setup |
| `DR_MONITOR_DISCORD_WEBHOOK` | DR freshness monitor | Same as `discord-alert-webhook` is fine |
| `CODERABBIT_API_KEY` | PR review | Optional, can skip during DR |

After a DR rebuild, audit `gh secret list --repo nomadkaraoke/karaoke-gen` to confirm everything is set.

## flacfetch (separate repo)

`flacfetch/infrastructure/` deploys its own GCE VM. Has its own secrets in GCP Secret Manager:

| Secret | Purpose |
|---|---|
| `flacfetch-account-email`, `flacfetch-account-password` | tracker logins |
| `flacfetch-api-key`, `flacfetch-api-url` | shared with karaoke-gen for job dispatch |
| `red-api-*`, `ops-api-*` | private tracker API |
| `spotify-cookie`, `spotify-oauth-token` | maintained by credential keeper |
| `youtube-cookies` | maintained by credential keeper |

These are also captured by the secrets backup (the backup function enumerates all secrets in the project, regardless of which repo "owns" them in IaC).
