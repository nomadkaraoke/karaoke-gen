# KaraokeHunt request interceptor (Cloudflare Worker)

Auto-converts song requests from the **retired KaraokeHunt mobile app** into
Nomad Karaoke users + jobs. Background: the app (unpublished from both stores
2026-09-13; its GCP backend is dead) still has installs whose "Request Now"
button POSTs to `https://create.karaokehunt.com/create_karaoke_video`, fires a
Pushbullet push to Andrew directly from the phone, and shows a hardcoded
"check your email in 5-10 minutes" success modal. ~5-10 requests/month still
arrive (2026). The binary cannot be changed; the domain can.

## How it works

```
app POST create.karaokehunt.com/create_karaoke_video
  └─ Worker (this dir): responds {"status":"success"} instantly,
     ctx.waitUntil-forwards the JSON to
       POST https://api.nomadkaraoke.com/api/karaokehunt/request
       with X-KH-Forwarder-Secret (Secret Manager: karaokehunt-forwarder-secret)
         └─ backend/workers/karaokehunt_conversion.py decides:
            ONE freebie EVER per email — repeats get only the throttled
              "please uninstall, use gen directly" email (repeat_request)
            community karaoke version already exists → no job; email links
              to it (community_existing; new users keep their credit)
            new email → create account +1 credit → job as them
            existing w/ credits → job (their credit)
            no credits / over daily cap → free community requests board
            …and sends the long-promised email (one-click sign-in link;
            every variant says: uninstall the app, use gen directly).
```

## Deploy / update

```bash
cd $(git rev-parse --show-toplevel)/infrastructure/cloudflare/karaokehunt-interceptor
source /Users/andrew/Projects/nomadkaraoke/.envrc   # CF token + account id
./deploy.sh
```

Idempotent. NOT Pulumi/wrangler-managed — the `karaokehunt.com` zone
(`02440ab623269c428be9e65a16bee280`, Andrew's account `a7dd2a2b…`) is outside
IaC, so this script + `docs/ARCHITECTURE.md` are the change record.

## Kill switch

Delete the Worker route (fast, reversible — requests fall through to the dead
origin again, i.e. pre-interceptor behaviour):

```bash
curl -sX GET "https://api.cloudflare.com/client/v4/zones/02440ab623269c428be9e65a16bee280/workers/routes" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"   # find route id
curl -sX DELETE ".../workers/routes/<id>" -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

Backend-side: disable by removing the `KARAOKEHUNT_FORWARDER_SECRET` mapping
from ci.yml `--set-secrets` (endpoint 503s when unset), or set
`KARAOKEHUNT_DAILY_JOB_CAP=0` to force everything to the requests board.

## Observability

- Firestore `karaokehunt_requests` — one doc per intake with `outcome`
  (`job_created` / `job_parked` / `board_submitted` / `community_existing` /
  `repeat_request` / `duplicate` / `invalid` / `error`), `job_id`, `email_sent`, raw payload.
- Cloud Logging: `karaokehunt:` log lines on karaoke-backend.
- Failed docs: retry via `POST /api/karaokehunt/internal/reprocess/{doc_id}`
  (admin token).
- Andrew's Pushbullet still gets the app's own client-side push per request —
  an independent human backstop that this pipeline never touches.

## Zone changes made 2026-09-22 (via CF API, recorded here per the infra rule)

Getting the Worker reachable required two `karaokehunt.com` zone changes beyond
`deploy.sh`'s worker/route/DNS:

1. **Catch-all redirect scoped.** Dynamic-redirect ruleset
   `41dc51daea2b4cb298ba73a19eb3dd9d`, rule "Redirect all to nomadkaraoke.com":
   expression changed `true` → `(http.host ne "create.karaokehunt.com")`.
   Redirect rules run BEFORE Worker routes, so the catch-all was 301ing the
   app's POSTs to nomadkaraoke.com. Apex/`www` redirects unchanged.
2. **Bot Fight Mode disabled** (`bot_management.fight_mode: true → false`).
   BFM served a managed JS challenge to non-browser clients (the app's Dart
   HTTP client, curl) ahead of the Worker and cannot be scoped by skip rules.
   The zone only serves redirects + this interceptor, so it was safe to drop.

To revert either, reverse the API calls (see git history of this file's session
record: `nomadkaraoke/docs/sessions/2026-Q3/2026-09-22-karaokehunt-app-interceptor.md`).
