# Wiring `requests.nomadkaraoke.com` → the voting board (Cloudflare)

The public voting board is a route in the gen Next.js app: it deploys automatically with
the `karaoke-gen` Cloudflare Pages project and is reachable at
`https://gen.nomadkaraoke.com/en/requests` the moment this PR merges.

To make the vanity URL `requests.nomadkaraoke.com` (the thing we link from every YouTube
description) resolve to the board, add a Cloudflare **redirect** — the same pattern the
referral interstitial uses (`nomadkaraoke.com/r/CODE` → gen). This is **not** in Pulumi;
`nomadkaraoke.com` DNS + rules are managed directly via the Cloudflare API/dashboard.

**Do this at SHIP time (after the PR is merged and Pages has deployed the `/requests`
route)** — otherwise the redirect points at a 404 until the deploy lands.

## Steps (dashboard — simplest, ~2 min)

1. **DNS**: Cloudflare → `nomadkaraoke.com` → DNS → add a **proxied** (orange-cloud) record
   so the hostname exists at the edge and rules can match it:
   - Type `CNAME`, Name `requests`, Target `gen.nomadkaraoke.com`, Proxy **ON**.
2. **Redirect Rule**: Rules → Redirect Rules → Create:
   - When incoming requests match: `Hostname` `equals` `requests.nomadkaraoke.com`
   - Then: Static redirect, URL `https://gen.nomadkaraoke.com/en/requests`, status **302**,
     preserve query string OFF.

That's it — `requests.nomadkaraoke.com` now lands users on the board. (302 not 301 so we can
later swap to a true custom-domain host without a cached permanent redirect fighting us.)

## Verify

```bash
curl -sI https://requests.nomadkaraoke.com | grep -i "location\|HTTP/"
# expect: 302 + location: https://gen.nomadkaraoke.com/en/requests
```

Then open it in a browser and confirm the board renders and a magic-link sign-in works.

## Later upgrade (optional, keeps the vanity host in the address bar)

Attach `requests.nomadkaraoke.com` as a **custom domain** on the `karaoke-gen` Pages project
and add a host-aware root rewrite (`/` on that host → `/en/requests`). `requests` is already in
the `nonTenantSubdomains` allow-list in `frontend/functions/[[path]].ts`, so it will not be
mistaken for a white-label tenant. Not needed for Phase 1 — the redirect above is sufficient.

## Record

Log any DNS/rule change made here (and in the workspace infra notes) per the standing rule that
Cloudflare API/dashboard changes leave no trace in code.
