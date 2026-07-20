# Edge Security — Cutover Runbook

Companion to `2026-07-20-edge-security-hardening-plan.md`. Step-by-step ops
sequence to put `api.nomadkaraoke.com` behind the Cloudflare edge with **near-zero
downtime**. Every prod-affecting action is a single, instantly-reversible flip.

**Who runs what:** all `pulumi`, `gcloud`, and Cloudflare-token steps are run by
**Andrew** (agent has read-only ADC). The code (Pulumi module, middleware, alert,
CI env) is already in this branch.

---

## Legend
- 🟢 non-disruptive (no prod impact)
- 🔴 prod-affecting (but instantly reversible)
- ✅ verification gate — do not proceed until green

---

## Prereqs (one-time) 🟢

1. **Cloudflare token** — the `.envrc` `CLOUDFLARE_API_TOKEN` (`nomadkaraoke-claude`)
   now has the required zone scopes (**verified 2026-07-20**: DNS record
   create+delete OK, rulesets list OK, zone settings editable). Point Pulumi at it:
   ```bash
   cd infrastructure
   pulumi config set --secret cloudflare:apiToken "$CLOUDFLARE_API_TOKEN"
   # zone id (807f07f458f9cd38251f3b7948d55172) is baked in as the default;
   # only set these if overriding:
   # pulumi config set edge:cloudflareZoneId    <zone-id>
   # pulumi config set edge:cloudflareAccountId <account-id>
   pulumi config set edge:rolloutStage        staging          # default anyway
   # Zone is on the FREE plan → managed OWASP ruleset stays OFF (Pro+ only).
   # Leave edge:managedWafEnabled unset; set true only after upgrading to Pro.
   ```
   > (Optional least-privilege: mint a dedicated token scoped to nomadkaraoke.com
   > with DNS:Edit, Zone WAF:Edit, Zone Settings:Edit, Transform Rules:Edit,
   > Zone:Read, and use that instead of the broad account token.)
   > **Re-check any token before enabling** (should print `success: True`):
   > ```bash
   > curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
   >   "https://api.cloudflare.com/client/v4/zones/807f07f458f9cd38251f3b7948d55172/dns_records?per_page=1" \
   >   | python3 -c "import sys,json;print('success:',json.load(sys.stdin)['success'])"
   > ```
2. **Create the secret container + other additive infra first** (edge module is
   still OFF — `edge:enabled` defaults to false, so NO Cloudflare calls happen):
   ```bash
   cd infrastructure && pulumi preview   # expect ONLY additions (incl. edge-origin-secret); no Cloudflare
   pulumi up
   ```
   ✅ No Cloudflare resources, no staging mapping, prod `api` untouched.

3. **Origin secret** — generate once, store in BOTH Secret Manager (backend reads
   it) and Pulumi config (Cloudflare injects it). Must be identical.
   ```bash
   SECRET=$(openssl rand -hex 32)
   printf '%s' "$SECRET" | gcloud secrets versions add edge-origin-secret \
     --data-file=- --project=nomadkaraoke      # container created in step 2
   pulumi config set --secret edge:originSecret "$SECRET"
   unset SECRET
   ```
   > ⚠️ The CI deploy references `edge-origin-secret:latest` — a backend deploy
   > will fail until this version exists. Add it **before** this branch merges.

4a. **Import the existing `api` DNS record** — it ALREADY exists in Cloudflare
   (id `6c31cba0080ff334a85cfff6c2927219`), so Pulumi must adopt it instead of
   creating a duplicate. Import format is `<zone-id>/<record-id>`:
   ```bash
   # edge:enabled must be true so the resource exists in the program (next step
   # sets it) — run the import AFTER `pulumi config set edge:enabled true`, or
   # temporarily enable, import, then continue.
   pulumi config set edge:enabled true
   pulumi import cloudflare:index/dnsRecord:DnsRecord api-dns \
     807f07f458f9cd38251f3b7948d55172/6c31cba0080ff334a85cfff6c2927219
   ```
   > The Pulumi resource name is `api-dns` (the `cloudflare.DnsRecord` in
   > `edge_security.create_dns_records`). If your program nests it, use the fully
   > qualified URN Pulumi prints. After import, `pulumi preview` should show the
   > record with **no changes** (proxied=False, ttl=1, content=ghs.googlehosted.com).
   > Zone `security_level` (already "medium") is set via `cloudflare.ZoneSetting`;
   > if preview wants to "create" it and errors that it exists, import similarly.

4b. **Activate + apply the edge module** (creates staging mapping + DNS + WAF +
   rate limit + header transform + zone settings, all scoped to the staging host
   only — prod `api` still DNS-only because `edge:rolloutStage=staging`):
   ```bash
   pulumi preview      # expect additions for the STAGING host; prod api record proxied=False, ttl=1 (UNCHANGED)
   pulumi up
   ```
   ✅ `pulumi preview` shows the imported prod `api` DnsRecord as **proxied=False**
   with no diff, and no replacement of existing resources. If the token is
   under-scoped, this fails cleanly (nothing prod-affecting changed) — fix and retry.

---

## Phase A — Ship code dormant 🟢

5. Merge this branch (or deploy backend) with `EDGE_AUTH_MODE=off`. The
   middleware is a no-op; the alert retune and staging infra are live.
   ✅ Prod behaves exactly as before: `curl -sI https://api.nomadkaraoke.com/`
   still shows `server: Google Frontend` (no cf-ray).

---

## Phase B — Validate everything on staging 🟢 ✅

6. Wait for the staging Cloud Run managed cert to provision (proves ACME works
   under our setup):
   ```bash
   gcloud beta run domain-mappings describe --domain api-edge-test.nomadkaraoke.com \
     --region us-central1 --project nomadkaraoke \
     --format='value(status.conditions[].type, status.conditions[].status)'
   # wait until CertificateProvisioned / Ready = True
   ```
7. Run the verification suite against **staging** (`HOST=api-edge-test.nomadkaraoke.com`):
   ```bash
   HOST=api-edge-test.nomadkaraoke.com
   curl -sI https://$HOST/ | grep -iE 'server|cf-ray'          # expect server: cloudflare + cf-ray
   for p in '.env' 'home/root/.ssh/id_rsa' 'wp-config.php' 'etc/passwd'; do
     echo -n "$p -> "; curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST/$p"; done   # expect 403
   curl -s -o /dev/null -w 'health %{http_code}\n' https://$HOST/api/health                # expect 200
   for i in $(seq 1 120); do curl -s -o /dev/null -w '%{http_code} ' https://$HOST/api/health; done; echo  # rate-limit trips
   ```
   ✅ cf-ray present, exploit paths 403, health 200, rate-limit engages, **no
   525/SSL handshake errors**.

8. **If SSL errors appear**, step down the ladder (edit `edge_security.py` /
   zone SSL mode) and re-test:
   - `Full (strict)` → `Full` (skip strict cert validation), or
   - add a Cloudflare **Origin Rule** overriding origin host + SNI to
     `karaoke-backend-ipzqd2k4yq-uc.a.run.app` (always-valid `*.run.app` cert).

9. **Origin-lock check** — deploy backend with `EDGE_AUTH_MODE=enforce` scoped
   to staging (or test with a temporary override), then:
   ```bash
   curl -sI https://karaoke-backend-ipzqd2k4yq-uc.a.run.app/ | head -1   # expect 403 (direct origin)
   curl -sI https://api-edge-test.nomadkaraoke.com/api/health | head -1  # expect 200 (via edge)
   ```
   ✅ Direct-origin blocked, edge-proxied allowed. Health/root still 200 direct
   (exempt).

10. **Soak** staging proxied for a few days; watch for delayed cert/525 issues
    and any WAF false positives (Cloudflare Firewall Events / Security dashboard).

---

## Phase C — (Pro only) enable + block the managed WAF ruleset 🟢

11. **Skip on the Free plan** — the managed OWASP ruleset is Pro+ and is gated
    off (`edge:managedWafEnabled` default false). If/when the zone is upgraded to
    Pro: `pulumi config set edge:managedWafEnabled true`, `pulumi up` (deploys it
    in **log** mode), review Firewall Events for false positives, then change
    `_managed_action = "log"` → `"block"` in `edge_security.py` and `pulumi up`.

---

## Phase D — Prod cutover 🔴 (instantly reversible)

Pick a low-traffic window. `gen.nomadkaraoke.com` depends on this API.

12. Set backend `EDGE_AUTH_MODE=warn` in prod first (observe, don't block) and
    redeploy. Watch logs: after cutover, requests via CF carry the header; if you
    see many "missing header" warnings for legit traffic, DO NOT enforce yet.
13. Flip prod to proxied:
    ```bash
    cd infrastructure
    pulumi config set edge:rolloutStage cutover
    pulumi preview        # expect: api DnsRecord proxied False->True; edge rules add prod host
    pulumi up
    ```
14. Verify against **prod** (`HOST=api.nomadkaraoke.com`) — same suite as step 7,
    plus a real product smoke test (load `gen.nomadkaraoke.com`, start a job,
    confirm signed-URL upload to GCS still works — uploads bypass CF so must be
    unaffected).
    ✅ cf-ray present, exploit paths 403, product works end-to-end.
15. Once stable (minutes-hours), set `EDGE_AUTH_MODE=enforce` in prod + redeploy.
    ✅ `curl -sI https://karaoke-backend-ipzqd2k4yq-uc.a.run.app/` → 403.

### 🔴 Rollback (any time within Phase D)
```bash
cd infrastructure
pulumi config set edge:rolloutStage staging   # prod api record → proxied=False
pulumi up                                      # reverts within one DNS TTL
```
And/or set `EDGE_AUTH_MODE=off` + redeploy backend. Uploads (signed-URL→GCS) are
never affected either way.

---

## Phase E — Cleanup 🟢

16. After prod is stable for ~1 week, remove the staging scaffold: delete
    `create_staging_domain_mapping()` + the staging DnsRecord from
    `edge_security.py` (and drop `STAGING_API_HOST` from the rule host lists),
    `pulumi up`. Keep everything else.

---

## Notes / gotchas captured
- **Uploads bypass the API** (signed-URL direct-to-GCS) → Cloudflare's 100MB
  proxy cap is a non-issue.
- **Schedulers** hit the public host `api.nomadkaraoke.com/api/internal/*` → they
  traverse CF and get the header automatically; they're **excluded from rate
  limiting** but **not** from the origin-lock (they're expected to carry the
  header). Their OIDC auth is unchanged.
- **Tenant subdomains** (`singa`, `vocalstar`) — live but idle. They're Cloudflare
  Pages frontends that call `api.nomadkaraoke.com` with an `X-Tenant-ID` header
  (their own `/api/*` 404s at Cloudflare — no backend passthrough). So they get
  the `X-Edge-Auth` header via the proxied `api` host like everyone else and are
  NOT expected to break. `warn` mode (step 12) is the safety net — if any tenant
  request shows up in the missing-header logs, fix before `enforce`. Downtime
  tolerable per Andrew.
- The error-rate alert is a **rate** (req/s), not a true %. Now scoped to 5xx so
  client/bot 404s never page.
