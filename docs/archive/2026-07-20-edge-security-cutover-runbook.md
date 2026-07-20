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

4. **Activate + apply the edge module (staging scope)** — creates the staging
   Cloud Run mapping + staging DNS + WAF/rate-limit/header-transform/zone-settings,
   all scoped to `api-edge-test.nomadkaraoke.com` only. The live prod `api` record
   is NOT managed at this stage (the module only manages it once rolloutStage=prod),
   so there is nothing to import yet and zero risk to prod DNS:
   ```bash
   pulumi config set --secret cloudflare:apiToken "$CLOUDFLARE_API_TOKEN"
   pulumi config set edge:enabled true          # rolloutStage defaults to "staging"
   pulumi preview   # expect: NEW staging resources only; prod `api` record NOT in the plan
   pulumi up        # (use --target on the edge URNs if the stack has unrelated drift)
   ```
   > The staging `api-edge-test` record is created **grey-cloud (proxied=False)**
   > on purpose — see step 6 (the SSL dragon). It gets flipped to orange after the
   > cert issues.
   > Zone `security_level` (already "medium") is set via `cloudflare.ZoneSetting`
   > — it adopts the current value (no-op). If preview errors that it "exists",
   > import it: `pulumi import cloudflare:index/zoneSetting:ZoneSetting zone-security-level 807f07f458f9cd38251f3b7948d55172/security_level`.
   >
   > **Free-plan gotchas already handled in code (learned 2026-07-20 apply):**
   > WAF uses the `contains` operator not regex `matches` (regex = Business+);
   > rate-limit `period` must be 10s and `characteristics` MUST include
   > `cf.colo.id`. Managed OWASP ruleset stays off (Pro+).

---

## Phase A — Ship code dormant 🟢

5. Merge this branch (or deploy backend) with `EDGE_AUTH_MODE=off`. The
   middleware is a no-op; the alert retune and staging infra are live.
   ✅ Prod behaves exactly as before: `curl -sI https://api.nomadkaraoke.com/`
   still shows `server: Google Frontend` (no cf-ray).

---

## Phase B — Validate everything on staging 🟢 ✅

6. **The SSL dragon (grey → provision → orange).** With the record grey-cloud,
   wait for the Cloud Run managed cert to provision (DNS must resolve to `ghs`
   for ACME — a proxied record blocks issuance):
   ```bash
   gcloud beta run domain-mappings describe --domain api-edge-test.nomadkaraoke.com \
     --region us-central1 --project nomadkaraoke \
     --format='value(status.conditions[].type, status.conditions[].status)'
   # wait until Ready=True / CertificateProvisioned=True (~15 min)
   ```
   Then flip the staging record to **orange-cloud** and choose SSL mode:
   ```bash
   pulumi config set edge:proxyStaging true
   pulumi up --target 'urn:...:DnsRecord::api-edge-test-dns'
   ```
   Zone SSL/TLS mode should be **Full (strict)**; if the proxied host then throws
   525/handshake, step down the ladder (step 8).
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

9. **Origin-lock check** — deploy backend with `EDGE_AUTH_MODE=enforce` (test
   env / temporary override), then hit a **non-exempt** path directly on the
   origin. Exempt paths are only `/`, `/api/health`, `/api/health/*` (probes) —
   testing those would return 200 and prove nothing, so use a real endpoint:
   ```bash
   RUNAPP=karaoke-backend-ipzqd2k4yq-uc.a.run.app
   curl -s -o /dev/null -w '%{http_code}\n' https://$RUNAPP/api/jobs            # expect 403 (direct, non-exempt)
   curl -s -o /dev/null -w '%{http_code}\n' https://$RUNAPP/api/health          # expect 200 (exempt probe path)
   curl -s -o /dev/null -w '%{http_code}\n' https://api-edge-test.nomadkaraoke.com/api/jobs  # expect NOT 403 (via edge → has header)
   ```
   ✅ Non-exempt path blocked direct-to-origin (403); same path via the edge is
   allowed; health/root stay 200 direct (exempt for probes).

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

## Phase D — Prod cutover (two isolated steps)

Pick a low-traffic window. `gen.nomadkaraoke.com` depends on this API. The prod
edge rules and the DNS proxy flip are now **separate** switches
(`edge:rolloutStage=prod` vs `edge:proxyProdApi=true`) so we provision + verify
the rules while the API is still DNS-only, then flip the proxy on its own.

12. **Provision prod edge rules (still DNS-only)** 🟢 — extends the WAF /
    rate-limit / header-transform rules to the prod host AND brings the prod
    `api` record under management (still proxied=False). Because the record
    already exists in Cloudflare, IMPORT it first so Pulumi adopts it instead of
    creating a duplicate:
    ```bash
    cd infrastructure
    pulumi config set edge:rolloutStage prod
    # Import the existing prod `api` CNAME (only now does the module manage it):
    pulumi import cloudflare:index/dnsRecord:DnsRecord api-dns \
      807f07f458f9cd38251f3b7948d55172/6c31cba0080ff334a85cfff6c2927219
    pulumi preview   # expect: edge rules add api.nomadkaraoke.com; api DnsRecord = NO diff (proxied=False, ttl=1)
    pulumi up
    ```
    ✅ `api` still resolves direct to `ghs` (no cf-ray); prod WAF/header rules now
    exist; the `api` record is managed with proxied=False. Nothing user-facing changed.

13. Set backend `EDGE_AUTH_MODE=warn` in prod and redeploy (observe, don't
    block). This is inert until step 14 makes CF inject the header, but staging
    it now means the very first proxied requests are only warned, not blocked.

14. **Flip the prod DNS proxy** 🔴 (the single reversible change):
    ```bash
    pulumi config set edge:proxyProdApi true
    pulumi preview   # expect: ONLY the api DnsRecord proxied False->True; no other diffs
    pulumi up
    ```
15. Verify against **prod** (`HOST=api.nomadkaraoke.com`) — same suite as step 7,
    plus a real product smoke test (load `gen.nomadkaraoke.com`, start a job,
    confirm signed-URL upload to GCS still works — uploads bypass CF so must be
    unaffected). Watch backend logs for `edge-auth` warn lines from any legit
    caller missing the header (incl. tenants) before enforcing.
    ✅ cf-ray present, exploit paths 403, product works end-to-end, no warn spam.
16. Once stable (minutes-hours) and warn logs are clean, set
    `EDGE_AUTH_MODE=enforce` in prod + redeploy.
    ✅ `curl -s -o /dev/null -w '%{http_code}\n' https://karaoke-backend-ipzqd2k4yq-uc.a.run.app/api/jobs` → 403.

### 🔴 Rollback (any time within Phase D)
```bash
cd infrastructure
pulumi config set edge:proxyProdApi false   # prod api record → proxied=False (rules stay provisioned)
pulumi up                                    # reverts within one DNS TTL
```
And/or set `EDGE_AUTH_MODE=off` + redeploy backend. Uploads (signed-URL→GCS) are
never affected either way.

---

## Phase E — Cleanup 🟢

17. After prod is stable for ~1 week, remove the staging scaffold: delete
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
  NOT expected to break. `warn` mode (steps 13/15) is the safety net — if any
  tenant request shows up in the missing-header logs, fix before `enforce`.
  Downtime tolerable per Andrew.
- The error-rate alert is a **rate** (req/s), not a true %. Now scoped to 5xx so
  client/bot 404s never page.
