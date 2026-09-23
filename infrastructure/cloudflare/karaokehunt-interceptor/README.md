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
            new email → create account +1 credit → job as them
            existing w/ credits → job (their credit)
            no credits / over daily cap → free community requests board
            …and sends the long-promised email (one-click sign-in link).
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
  (`job_created` / `job_parked` / `board_submitted` / `duplicate` / `invalid` /
  `error`), `job_id`, `email_sent`, raw payload.
- Cloud Logging: `karaokehunt:` log lines on karaoke-backend.
- Failed docs: retry via `POST /api/karaokehunt/internal/reprocess/{doc_id}`
  (admin token).
- Andrew's Pushbullet still gets the app's own client-side push per request —
  an independent human backstop that this pipeline never touches.
