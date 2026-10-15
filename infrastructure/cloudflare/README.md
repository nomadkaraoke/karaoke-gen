# nomad-cloudflare: the Nomad Karaoke Cloudflare account as code

Pulumi project for the dedicated **Nomad Karaoke** Cloudflare account
(`4585249dcf5d2f9fb4e075c5382cd071`, owner admin@nomadkaraoke.com). Stack: `nomadkaraoke/prod`
on Pulumi Cloud (`nomadkaraoke/nomad-cloudflare/prod`).

It sits beside the GCP platform project in `infrastructure/`. Both are **shared platform IaC**:
`karaoke-gen/infrastructure` is the home for infra that isn't specific to one app. App repos keep
only app-specific stacks (e.g. karaoke-decide's Cloud Run + its API-proxy Worker).

## What's managed here

| Module | Resources |
|---|---|
| `zones.py` | The 5 zones (**protected**) and the `ssl` mode of each |
| `dns.py` | 84 DNS records across all zones (exclusions below) |
| `rulesets.py` | Zone ruleset entrypoints: dynamic redirects (all zones), config settings + origin rules (nomadkaraoke.com: flacfetch), karaokehunt.com's custom WAF rule |
| `email_routing.py` | Gmail destination address; Email Routing enablement (**protected**), catch-all and `google@` rules for nomadkaraoke.com, nomadkaroke.com, karaokehunt.com |
| `tunnels.py` | `nomad-kjbox` (NomadPC: sing, kjbox, kjssh, kjvnc) and `nomad-hf2` (fiverrbot) tunnels (**protected**) + ingress |
| `access.py` | Zero Trust org (**protected**), one-time-PIN IdP, `claude-code-kjbox` service token, reusable policies and the KJ Controller / FiverrBot noVNC apps |
| `pages_workers.py` | Pages projects `karaoke-gen` + `karaoke-gen-tenant`, `gen.nomadkaraoke.com` Pages domain, nomadkaraoke.com/www Worker custom domains, `create.karaokehunt.com/*` route |

All resource IDs (`ids.py`) are non-secret. Removing a non-protected resource from code deletes it
on the next `pulumi up`, and `pulumi preview` always shows that first.

## Deliberately NOT managed here

| What | Owner |
|---|---|
| `api` / `api-edge-test` DNS, nomadkaraoke.com `http_request_firewall_custom` / `http_ratelimit` / `http_request_late_transform` phases, `security_level` | GCP platform project, `infrastructure/modules/edge_security.py` |
| Tenant portal CNAMEs (`<tenant>.nomadkaraoke.com`) and `karaoke-gen-tenant` custom domains | Created at runtime by `backend/services/tenant_domain_service.py` |
| `decide.nomadkaraoke.com/api/*` route + `karaoke-decide-api-proxy` Worker | karaoke-decide's Pulumi stack |
| Worker/Pages **code** deploys | gen CI (`wrangler pages deploy`), public-website Workers Builds, `karaokehunt-interceptor/deploy.sh` (its route + DNS "ensure" steps match what's declared here) |
| Read-only DNS records (Email Routing MX/DKIM, Worker custom-domain AAAAs) | Owned by Email Routing / Workers custom domains |
| karaokehunt.com `http_config_settings` ("SSL for create.karaokehunt.com", "Disable challenge for create API") | Dashboard. pulumi-cloudflare 6.22.1 drops `set_config bic:false`, so every refresh fails |
| Tunnel connector tokens / cloudflared processes | NomadPC systemd `cloudflared-nomad`, hf2 `~/nomad-tunnel` compose |
| Registrar, billing, API tokens, account members | Dashboard (admin@nomadkaraoke.com) |

## Usage

```bash
cd infrastructure/cloudflare
python3 -m venv venv && venv/bin/pip install -r requirements.txt   # first time
pulumi stack select nomadkaraoke/prod
pulumi preview      # must be clean (0 changes) before you start
# edit code, preview, then:
pulumi up
```

Unit tests for the import-list builder: `venv/bin/pip install pytest && venv/bin/python -m pytest tests -c /dev/null`
(`-c /dev/null` skips the repo-root pytest config, which needs the backend's plugins).

As with the GCP project, **CI does not run Pulumi**. Apply locally, then merge the PR.

The `cloudflare:apiToken` in `Pulumi.prod.yaml` is the account-owned token
`nomad-cloudflare-pulumi`. It is scoped to zones/DNS/rules/email routing/Workers routes on all
zones, plus tunnels/Access/Pages/Worker domains on the account, and cannot manage tokens or billing.

## Adopting resources created outside Pulumi

`scripts/build_import_list.py` lists the account's resources minus the exclusions above in
`pulumi import --file` format:

```bash
source ../../../.envrc   # workspace: CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID (Nomad account)
python3 scripts/build_import_list.py > /tmp/import.json
# keep only the new entries, then:
pulumi import --file /tmp/import.json --out /tmp/generated.py
```

Paste the generated code into the right module, replace literal IDs with `ACCOUNT_ID` /
`ZONE_IDS[...]`, and drop `protect=True` unless it belongs to the protected set above.

## Provider gotchas (pulumi-cloudflare 6.22.1)

- Codegen drops `false` values. It lost `bic: false` above, and `support_subaddress=False` must be
  explicit on `EmailRoutingSettings` or every preview shows a no-op update.
- After an import, Email Routing / Access resources can show an "update" with no property diff.
  It is a one-time state settlement, and applying it was verified as a live no-op (2026-10-15).
- Reusable Access policies default to a 24h session. Set `session_duration` to match the app (730h).
- `ZeroTrustAccessApplication`: use `destinations`, not the deprecated `self_hosted_domains` (both together fail validation).
