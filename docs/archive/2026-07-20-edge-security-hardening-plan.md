# Edge Security Hardening — Cloudflare WAF for `api.nomadkaraoke.com`

**Date:** 2026-07-20
**Branch:** `feat/sess-20260720-1407-edge-security-hardening`
**Trigger:** Prod alert "Karaoke Backend - High Error Rate" fired 2026-07-20 17:43 UTC (0.167 vs 0.1 threshold).
**Prime directive:** *No extended downtime on the product.* `gen.nomadkaraoke.com`
(frontend) depends on `api.nomadkaraoke.com`. Validate every unknown on a
throwaway staging hostname first; the only prod-affecting action is a single,
instantly-reversible DNS proxy flip.

---

## 1. Incident recap & root cause

A single IP (`195.178.110.105`) ran an automated vulnerability scan against
`api.nomadkaraoke.com` — 323 requests/24h, ~1 every 2s — probing secret-exposure
paths (`/.env`, `/etc/passwd`, `/home/*/.ssh/id_rsa`, `wp-config.php`,
`.aws/credentials`, `/proc/self/environ`, …). **Every request returned 404** —
nothing exposed, no functional impact.

It tripped the alert only because on a low-traffic backend, 312/348 requests that
hour were bot 404s (~90%), and the alert counts **all non-2xx** with **no minimum
request-count floor**.

**Why we were reachable — the real gap:**

| Surface | Edge today |
|---|---|
| `nomadkaraoke.com`, `www`, `decide`, `gen` (frontend) | ✅ Proxied through Cloudflare (orange-cloud Pages) |
| **`api.nomadkaraoke.com`** (karaoke-backend) | ❌ **DNS-only** CNAME → `ghs.googlehosted.com`; Cloud Run **ingress=`all`**; no WAF, no rate limit |

Confirmed live: `curl -sI https://api.nomadkaraoke.com/` → `server: Google
Frontend`, no `cf-ray`. `infrastructure/__main__.py:665-673` even exports the
intended DNS record with `"proxied": False`. The API origin is our one
internet-facing surface with zero edge protection, served via a Cloud Run
**domain mapping** (not a Load Balancer) — which is why Cloud Armor can't attach
without re-architecting.

## 2. Decisions (confirmed with Andrew)

- **Architecture:** Cloudflare edge (orange-cloud the API + Cloudflare WAF).
- **Management:** Pulumi IaC (`pulumi_cloudflare`), version-controlled.
- **Scope:** Full — block + WAF/rate-limiting + origin lockdown + alert tuning.
- **Constraint:** ≤1h total downtime, ideally ~zero; test all unknowns off-prod.

## 3. Confirmed facts (from investigation)

- **Backend = FastAPI** (`backend/main.py:147`); existing middleware chain
  (`AuditLoggingMiddleware`, `TenantMiddleware`) — clean insertion point.
- **Tenant subdomains — investigated 2026-07-20.** `singa.nomadkaraoke.com` and
  `vocalstar.nomadkaraoke.com` are **live but idle**. They are **Cloudflare Pages
  frontends** (already proxied; `/api/*` on them 404s at Cloudflare — no backend
  passthrough). All their API traffic goes to `api.nomadkaraoke.com` with an
  **`X-Tenant-ID` header** (`frontend/lib/tenant.ts`, `lib/api.ts` →
  `API_BASE_URL=api.nomadkaraoke.com`), incl. tenant logo assets. So once `api`
  is proxied they get the `X-Edge-Auth` header like everyone else — **edge-auth
  should NOT break them.** `TenantMiddleware`'s Host-based detection path is
  dormant (the backend never receives Host=`*.nomadkaraoke.com` tenant hosts).
  Idle + downtime-tolerant per Andrew; `warn` mode catches any missed path.
- **Schedulers call the public host** `https://api.nomadkaraoke.com/api/internal/*`
  (`__main__.py:347-445`) via OIDC. Once proxied they traverse Cloudflare and get
  the injected secret header automatically — **but must be excluded from rate
  limits** (cron bursts) and their OIDC auth is unchanged.
- **Uploads bypass the API** — signed-URL direct-to-GCS
  (`karaoke_gen/utils/remote_cli.py:440`, "bypass Cloud Run's 32MB request body
  limit"). So Cloudflare's 100MB proxy cap is a **non-issue**; the API is a
  control plane (small JSON, async jobs via Cloud Tasks).
- **Only one domain mapping today** (`api.nomadkaraoke.com → karaoke-backend`,
  Ready) → we can add a **second staging mapping to the same service** for
  end-to-end edge testing without touching prod.
- **Cloudflare token — RESOLVED 2026-07-20.** The `.envrc` `CLOUDFLARE_API_TOKEN`
  is the account token `nomadkaraoke-claude` (account `a7dd2a2b…`). It was
  originally missing zone DNS/WAF/settings access (DNS records + rulesets +
  settings all 403); Andrew added the scopes. **Re-verified:** DNS record
  create+delete succeeds, rulesets list 200, zone settings readable/editable. So
  `cloudflare:apiToken` can reuse this token. (It's a broad account-admin token;
  a dedicated least-privilege zone-scoped token would be nicer but isn't required.)
- **Zone plan = Free** (verified). The **Cloudflare Managed (OWASP) ruleset is
  Pro+ only**, so it's **gated off by default** (`edge:managedWafEnabled`,
  default false) — deploying it on Free errors. Free DOES support everything
  else we use: custom firewall rules (≤5), one rate-limit rule, Transform Rules
  (header inject), Bot Fight Mode, Security Level — enough for the path-scanning
  threat. Pro (~$20/mo) later unlocks OWASP CRS.
- **Zone id:** `807f07f458f9cd38251f3b7948d55172` (baked in as the default in
  `config.py`; overridable via `edge:cloudflareZoneId`).
- **Activation gate:** the whole module is a no-op until `edge:enabled=true`
  (default false), so merging it can't make `pulumi up` attempt Cloudflare calls
  with the wrong token.

## 4. The dragon: Cloud Run domain mapping × Cloudflare proxy (TLS) — RESOLVED

**Investigated + resolved live on staging 2026-07-20.** The theory:
- A Cloud Run domain mapping provisions a Google-managed cert via ACME, which
  needs the host to resolve directly to `ghs` — a proxied (orange) record blocks
  issuance; and renewal (~90d) could fail behind the proxy → delayed 525/526.

**What we actually found (staging `api-edge-test.nomadkaraoke.com`):**
- Fresh managed-cert provisioning genuinely **does not work** behind Cloudflare
  even grey-cloud — the ACME challenge is never "visible" (confirmed correct DNS,
  propagation, no CAA; recreating the mapping didn't help). So the managed cert
  stays "pending" indefinitely.
- **BUT the zone SSL mode is "full" (not strict)** — and in "full" mode Cloudflare
  encrypts to the origin **without validating the origin cert**. So through the
  proxy, the managed cert's status is **irrelevant**: `ghs` serves the request
  over TLS that "full" mode accepts, and Cloud Run routes via the domain mapping.
- **Result: the proxied edge works end-to-end** — `GET /` → 200 + `cf-ray` + real
  backend JSON; exploit paths → 403 (WAF); `/api/health` → 200.

**Conclusion — no ladder, no run.app Origin Rule, no LB needed.** "Full" mode is
inherently renewal-safe (a never-provisioning / never-renewing managed cert can't
525 when the cert isn't validated). The only rule: **keep the zone on "full"; do
NOT switch to "full (strict)"** unless the managed cert is actually valid (it
can't be, behind CF). The run.app-SNI-override (Origin Rule) was implemented and
then removed — it's blocked anyway by the zone's singleton `http_request_origin`
entrypoint (already used by a flacfetch prod rule) and adds no benefit over "full".

**Prod cutover is therefore simple:** the prod `api` domain mapping already has a
(historically-provisioned) cert, and "full" mode tolerates it regardless — so the
cutover is just the DNS proxy flip; no cert wait, no 525 risk.

## 5. Phased plan (zero-downtime by construction)

### Phase 0 — Immediate mitigation (ship now, independent)
No dependency on the rest; stops paging + blocks the active scanner.
- **0a. Alert retune** (`monitoring.py`, §Phase E) — merge/apply first so scanner
  404s stop paging regardless of the WAF timeline.
- **0b. Temporary block** — Cloudflare IP Access Rule / WAF custom rule for
  `195.178.110.105` + exploit-path signature (dashboard now, codified in Phase D).

### Phase A — Pulumi Cloudflare bootstrap (no prod effect)
- Add `pulumi-cloudflare` to `infrastructure/requirements.txt`.
- Token as **Pulumi config secret**: `pulumi config set --secret cloudflare:apiToken <token>`.
- Add `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_ACCOUNT_ID` to `config.py`.
- New module `infrastructure/modules/edge_security.py` (all CF resources; nothing
  proxied yet). `pulumi preview` must show only *additive* resources.

### Phase B — Staging validation (the core de-risking; no prod effect)
Prove the entire edge stack on a throwaway host pointing at the **same** service.
1. Add **staging domain mapping** `api-edge-test.nomadkaraoke.com → karaoke-backend`.
2. Add staging **DNS record** grey-cloud (`proxied=False`) → `ghs.googlehosted.com`;
   **wait for the Cloud Run managed cert to provision** (proves ACME path).
3. Flip staging DNS to **orange-cloud** with **Full (strict)**; run the
   verification suite (§6) against `api-edge-test`:
   - `cf-ray` present, 200 on `/`, real endpoints work.
   - Exploit paths return **403 from Cloudflare** (not 404 from origin).
   - Rate-limit rule trips under a burst; `/api/internal/*` excluded.
   - Origin-secret-header: requests via CF pass; direct-to-`*.run.app` gets 403
     once middleware is in enforce mode.
   - No TLS errors; if 525/handshake issues → step down the SSL ladder (§4) and
     re-test until green.
4. **Soak:** leave staging proxied a few days; watch for delayed cert/525 issues.
5. Tear down staging mapping/record after cutover (documented).

### Phase C — Backend origin-lockdown middleware (deploy dormant)
- New `backend/middleware/edge_auth.py` (`BaseHTTPMiddleware`), added after CORS.
- Modes via env: `EDGE_AUTH_MODE=off|warn|enforce` (default `off`), secret from
  `EDGE_ORIGIN_SECRET` (Secret Manager). In `warn` it logs missing/invalid header
  but allows; in `enforce` it 403s. **Exempt** Cloud Run health/startup probes and
  keep it a no-op when `off`, so deploying it changes nothing until we flip modes.
- Unit tests for off/warn/enforce + exempt paths + tenant-host requests.
- Deploy to prod in `off`/`warn` — zero behavior change — before any DNS flip.

### Phase D — WAF / rate-limit / bot rules (staging-scoped, then global)
`cloudflare.Ruleset` resources, initially matched to the **staging host only**,
promoted to zone-wide after staging passes:
1. **Custom firewall** (`http_request_firewall_custom`): block exploit paths
   (`\.(env|git|aws)|/\.ssh/|wp-(config|login|admin)|/etc/|/proc/|configuration\.php|\.(mysql|bash)_history`).
2. **Rate limiting** (`http_ratelimit`): >~60 req/min per IP → block; exclude
   `/api/internal/*` and known tenant hosts.
3. **Managed ruleset** (`http_request_firewall_managed`): Cloudflare core managed
   rules in **log** mode → review → `block`.
4. **Bot Fight Mode** + zone Security Level ≥ Medium.

### Phase E — Alert retune (`infrastructure/modules/monitoring.py`, ~72-105)
Current: counts all non-2xx, `threshold=0.1`, no volume floor.
- Scope to **`response_code_class="5xx"`** (real server errors), or exclude 4xx.
- Add a **minimum-volume gate** (e.g. AND a condition on absolute 5xx count > N/5m)
  so a few requests can't trip the ratio.
- Update/add regression test if alert tests exist.

### Phase F — Prod cutover (two isolated steps; instant rollback)
Preconditions: Phases B–E green; middleware live in `warn` in prod. The prod
edge rules and the DNS proxy flip are **separate** switches so we never change
both at once (per CodeRabbit review):
1. **Provision prod rules, still DNS-only** (non-disruptive): `edge:rolloutStage=prod`
   + `pulumi up`. Prod WAF/rate-limit/header rules now exist; `api` still resolves
   direct to `ghs` (unchanged).
2. **Flip the DNS proxy** (the only prod-affecting change): `edge:proxyProdApi=true`
   + `pulumi up` — this changes ONLY the `api` record to proxied.
3. Run the §6 verification suite against `api.nomadkaraoke.com`; watch `edge-auth`
   warn logs for legit callers missing the header.
4. Rollback if needed → **`edge:proxyProdApi=false` + `pulumi up`** reverts within
   one TTL (`ttl=1`/auto), leaving the edge rules provisioned. Upload flow
   (signed-URL→GCS) is unaffected throughout.
5. Once stable + warn logs clean, flip middleware to `enforce`; remove the
   temporary Phase-0b block (now covered by the codified rules).

## 6. Verification suite (run against staging, then prod)
```bash
HOST=api-edge-test.nomadkaraoke.com   # then api.nomadkaraoke.com at cutover
RUNAPP=karaoke-backend-ipzqd2k4yq-uc.a.run.app

# Proxied by Cloudflare (expect server: cloudflare + cf-ray)
curl -sI https://$HOST/ | grep -iE 'server|cf-ray'

# Exploit paths blocked at edge (expect 403 from Cloudflare, not 404)
for p in '.env' 'home/root/.ssh/id_rsa' 'wp-config.php' 'etc/passwd'; do
  echo -n "$p -> "; curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST/$p"; done

# Legit control-plane still works (expect 200)
curl -s -o /dev/null -w '%{http_code}\n' https://$HOST/api/health

# Rate limit trips under burst (expect some 429/403 after threshold)
for i in $(seq 1 120); do curl -s -o /dev/null -w '%{http_code} ' https://$HOST/api/health; done; echo

# Origin bypass rejected once middleware enforced (expect 403)
curl -sI https://$RUNAPP/ | head -1

# No long (>100s) synchronous endpoints behind proxy — review slow-request logs
```

## 7. Files to change
1. `infrastructure/requirements.txt` — add `pulumi-cloudflare`.
2. `infrastructure/config.py` — CF zone/account ids, token secret ref.
3. `infrastructure/modules/edge_security.py` — **new**: staging+prod DnsRecord,
   Rulesets (custom/rate-limit/managed), Transform Rule (secret header), staging
   domain mapping, zone settings.
4. `infrastructure/__main__.py` — wire module; replace `proxied:False` export
   (~665-673); staging mapping export.
5. `infrastructure/modules/monitoring.py` — retune error-rate alert (~72-105).
6. `backend/middleware/edge_auth.py` — **new** middleware + register in
   `backend/main.py` after CORS; env-driven modes.
7. `backend/config` / deploy env — `EDGE_AUTH_MODE`, `EDGE_ORIGIN_SECRET`.
8. Tests: `tests/.../test_edge_auth_middleware.py`; monitoring test if present.
9. Docs: `infrastructure/README.md` + this runbook.

## 8. Rollback summary (per phase)
- A/C/D/E deploy dormant or additive → revert = normal `pulumi up` / redeploy.
- **B** staging is throwaway → delete mapping+record, zero prod impact.
- **F** cutover → `proxied=False` + `pulumi up` (≤ one TTL). Middleware → set
  `EDGE_AUTH_MODE=off`.

## 9. Open questions
Resolved:
- ✅ **Zone id** = `807f07f458f9cd38251f3b7948d55172` (baked in).
- ✅ **Plan tier** = Free → managed WAF ruleset gated off (Pro+ only); rest works.
- ✅ **Token** — the `.envrc` `CLOUDFLARE_API_TOKEN` now has DNS/WAF/settings
  scopes (verified via live create+delete). Reuse it for `cloudflare:apiToken`.
- ✅ **Existing `api` DNS record** (id `6c31cba0080ff334a85cfff6c2927219`,
  proxied=False, ttl=1) — must be `pulumi import`ed, not created (runbook step 4a).

- ✅ **Tenant subdomains** (`singa`, `vocalstar`) — live but idle Pages frontends
  that call the proxied `api` host with `X-Tenant-ID`; not broken by edge-auth
  (see §3). Downtime-tolerant per Andrew.

Still open:
- Confirm **no synchronous endpoint** routinely runs >100s under proxy (checked
  during staging soak via slow-request logs).
- Do we also want to protect other direct GCP origins (audio-separator,
  flacfetch) as a follow-up — same exposure class.
