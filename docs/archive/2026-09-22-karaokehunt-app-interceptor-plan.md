# KaraokeHunt app-request interceptor → auto-convert to Gen users (2026-09-22)

## Context

The retired KaraokeHunt mobile app (stores unpublished 2026-09-13, old GCP project dead) still
has real installs submitting song requests: **~5–10/month through 2026**. The app's request flow
is entirely client-side: it POSTs to `https://create.karaokehunt.com/create_karaoke_video`
(dead endpoint, stale A record), fires a Pushbullet push to Andrew directly from the phone
(hardcoded token in the binary), and shows a hardcoded success modal promising *"You should
receive an email in 5-10 minutes with a link to your new karaoke video on YouTube!"* — a promise
that has been silently broken for years.

Andrew's spec (2026-09-20, BACKLOG INBOX, extends Frog #2):

> is there any way we could set it up to auto-convert these users into karaoke-gen users
> instead, whenever they request a song (eg. sign them up for karaoke-gen using the email
> address, perhaps send them an email explaining how they can make the song via karaoke-gen)

> hopefully we can auto-convert emails which haven't yet got a karaoke-gen account into new
> users and auto-submit the job for them based on the artist and title they entered, or for
> users who have already got an account and have no free credits, perhaps it could submit their
> request to the free (1 track per day) requests board (https://requests.nomadkaraoke.com)
> instead or something? in either case the goal is to auto-convert these old karaokehunt
> installs into Gen users

The app ignores the HTTP response entirely (unconditional success modal), so in-app messaging is
impossible — but unnecessary: the modal already says "check your email". We just make that true.

## Architecture

```
old app (unchangeable binary)
  POST https://create.karaokehunt.com/create_karaoke_video   {email, artist, title, input_url, …}
    │  Cloudflare Worker "karaokehunt-interceptor" (karaokehunt.com zone, Andrew's CF account)
    │  – responds 200 {"status":"success"} to the app IMMEDIATELY
    │  – ctx.waitUntil(forward) → backend does the slow work after the app is answered
    ▼
  POST https://api.nomadkaraoke.com/api/karaokehunt/request
    headers: X-KH-Forwarder-Secret (shared secret), X-KH-Client-IP
    │  unauthenticated route, gated by the shared secret + per-IP rate limit
    │  writes intake doc → runs conversion synchronously → updates doc outcome
    ▼
  Firestore `karaokehunt_requests` (audit/tracking) + user/job/board/email side effects
```

- **Worker responds instantly** so the app's awaited call never hangs; the backend conversion
  (flacfetch search can take ~40s) continues via `waitUntil` — and even if the subrequest is
  reaped, uvicorn finishes the handler.
- **No new queues/infra**: synchronous conversion mirrors `community_daily_pick` (the
  established "grant credit + create job as user + search/auto-select/download" precedent).
  Durability: failed conversions stay `status=error` in `karaokehunt_requests`; an internal
  admin endpoint `POST /api/internal/karaokehunt/reprocess/{doc_id}` retries; the Pushbullet
  push to Andrew (untouched, fires from the phone) is the human backstop.
- **Deploys dark**: the route 503s until `KARAOKEHUNT_FORWARDER_SECRET` is wired
  (Secret Manager `karaokehunt-forwarder-secret` + ci.yml `--set-secrets`), and receives no
  traffic until the CF Worker + DNS go live. Kill switch = disable the Worker route.

## Conversion decision table

| Case | Action | Email variant |
|---|---|---|
| New email (no gen account) | Create user + 1 credit (`karaokehunt_app_conversion`), create job **as them** (consumes it), search → conservative auto-select → download | "Your track is being made" + one-click login to the job. Welcome credit intentionally NOT pre-granted — first sign-in still awards it (their next song is covered). |
| Existing user, credits ≥ 1 | Create job as them (consumes 1 credit) | Same, + "used 1 of your credits — reply if that's not OK" |
| Existing user, 0 credits | `song_request_service.submit_request` → requests board (dedup → upvote) | "Added to the free daily community board — vote here" |
| Search finds no confident match (`pick_auto_selection` None / no results) | Job parked `AWAITING_AUDIO_SELECTION` | "Click to pick the right recording (10 seconds)" via one-click login |
| Global daily cap reached (default 3 jobs/day) | Fall through to requests-board path (still creates account + credit for new users) | Board variant |
| Duplicate (same email+song, ≤14 days, prior terminal outcome) | Nothing (logged) | none |
| Garbage/invalid email | Log only | none |

Extra touch: in-process KaraokeNerds check (`check_community_versions`) — when a community
version already exists, every email variant appends "you can sing it right now: [YouTube link]".

Conservative auto-select (`pick_auto_selection`, not `select_best`) deliberately: KH inputs
include garbage ("Ddd – Ddd"); public YouTube publishes must not be triggered by low-confidence
matches. Parked jobs cost nothing until the user picks.

## Abuse posture

Endpoint is unauthenticated by necessity (the app can't auth) but: shared secret known only to
the CF Worker; per-IP `RateLimiter` (10/min, client_events pattern); global daily job cap
(`KARAOKEHUNT_DAILY_JOB_CAP`, default 3 — bounds GPU spend at ~$5/day worst case); 14-day
per-email+song dedup; credits only granted when a job is actually submitted. Volume is
~1 request every 3 days, so the caps are generous.

## Email (Andrew must review copy — EMAIL POLICY)

One template `send_karaokehunt_conversion` with conditional blocks, localized keys under
`emails.karaokehuntConversion.*` (en written, all 33 locales via translate.py; sent with
locale="en" since the app gives no locale signal). Copy drafted in the PR; flag to Andrew in
the session summary — ships live, but at current volume he can amend before the next request
in practice.

## Files

- `backend/api/routes/karaokehunt.py` — intake + internal reprocess routes
- `backend/workers/karaokehunt_conversion.py` — conversion logic
- `backend/services/email_service.py` — `send_karaokehunt_conversion`
- `backend/translations/en.json` (+ 32 locales) — email strings
- `backend/config.py` — `karaokehunt_forwarder_secret`, `karaokehunt_daily_job_cap`
- `backend/main.py` — router registration
- `.github/workflows/ci.yml` — `KARAOKEHUNT_FORWARDER_SECRET=karaokehunt-forwarder-secret:latest`
- `infrastructure/cloudflare/karaokehunt-interceptor/` — worker.js + deploy script + README
  (zone `karaokehunt.com` = `02440ab623269c428be9e65a16bee280`, NOT Pulumi-managed; changes
  recorded per the standing infra-doc rule)
- Tests: `backend/tests/test_karaokehunt_intake.py`, `test_karaokehunt_conversion.py`

## Live verification plan

POST to `create.karaokehunt.com/create_karaoke_video` with a test email mimicking the app
payload → verify: instant 200; `karaokehunt_requests` doc; gen account + credit; job running;
email received (Andrew's own address); requests-board fallback with a 0-credit account.
