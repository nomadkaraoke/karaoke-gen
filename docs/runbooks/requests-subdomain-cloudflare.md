# `requests.nomadkaraoke.com` → the voting board (Cloudflare)

The public voting board is a static route in the gen Next.js build
(`https://gen.nomadkaraoke.com/en/requests/`). Since 2026-10-19 it is **served on**
`requests.nomadkaraoke.com` itself, not redirected, so the short URL stays in the address bar
(Andrew copies it from his phone into YouTube comment replies).

## How it works

| Piece | Where |
|---|---|
| `requests` CNAME → `gen.nomadkaraoke.com`, proxied | Pulumi, `infrastructure/cloudflare/dns.py` |
| Worker route `requests.nomadkaraoke.com/*` → `requests-host` | Pulumi, `infrastructure/cloudflare/pages_workers.py` |
| Worker script `requests-host` | `infrastructure/cloudflare/requests-host/worker.js`, deployed by `deploy.sh` |

The Worker:

- `/` → fetches gen `/{locale}/requests/` and returns it (query string kept, e.g. `utm_*`).
  Locale = the `nk_requests_locale` cookie (set by the board's language switcher on this host),
  else the browser's `Accept-Language` (same primary-subtag matching as gen's `LocaleRedirect`),
  else `en`. Response carries `Vary: Accept-Language, Cookie`.
- `/{locale}` or `/{locale}/` (e.g. `/es/`) → that language's board, for explicit links.
- paths with a file extension (`/_next/*`, fonts, icons, `sw.js`, RSC `.txt`) → proxied from gen.
- any other page path → 302 to the same path on gen, so the app never runs on this origin.

The frontend treats `requests` as a non-tenant subdomain (`frontend/lib/edge/nonTenantSubdomains.ts`,
`backend/middleware/tenant.py`), the board header's language switcher sets the cookie and reloads `/` on this host (so the
shareable URL stays bare), and the board's "Make it yourself" button leaves for
`gen.nomadkaraoke.com/app` when on this host. Board sign-in magic links still land on gen
(`/en/auth/verify` → `/requests`), so a user who signs in ends up on the gen copy of the board.

## Changing it

```bash
cd infrastructure/cloudflare/requests-host
node --test .                       # unit tests
set -a; source ../../../../.envrc; set +a   # workspace .envrc (Nomad CF account)
./deploy.sh                         # upload script
```

Route/DNS changes go through `pulumi up` in `infrastructure/cloudflare/`.

## Verify

From the repo root:

```bash
curl -sI https://requests.nomadkaraoke.com/          # 200, text/html, no location header
curl -sI https://requests.nomadkaraoke.com/en/app/   # 302 → https://gen.nomadkaraoke.com/en/app/
cd frontend && npx playwright test requests-vanity-host.spec.ts --config=playwright.production.config.ts
```

## Don't: Pages custom domain + rewrite rule

Tried 2026-10-19: attaching `requests.nomadkaraoke.com` as a custom domain on the `karaoke-gen`
Pages project made Cloudflare serve the gen **homepage** there, ignoring the zone redirect rule.
(The Pulumi token also lacks Transform Rules permission.) Rolled back within ~3 minutes.
The Worker avoids both problems.

## Rollback

Restore the old redirect: remove the Worker route in `pages_workers.py` and re-add a
`http_request_dynamic_redirect` rule `(http.host eq "requests.nomadkaraoke.com")` → 302
`https://gen.nomadkaraoke.com/en/requests` in `rulesets.py`, then `pulumi up`.
