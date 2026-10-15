#!/usr/bin/env python3
"""Build a `pulumi import --file` list of the Nomad Karaoke Cloudflare account's resources.

Used once to adopt the hand-made config into this stack (2026-10), and reusable to adopt
anything created outside Pulumi later. Reads the live account with CLOUDFLARE_API_TOKEN /
CLOUDFLARE_ACCOUNT_ID and prints JSON to stdout:

    python scripts/build_import_list.py > /tmp/import.json
    pulumi import --file /tmp/import.json --out /tmp/generated.py

Resources managed elsewhere are skipped on purpose (see EXCLUDED_* below and README.md).
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]
ACCOUNT = os.environ["CLOUDFLARE_ACCOUNT_ID"]

# Owned by karaoke-gen's GCP platform stack (infrastructure/modules/edge_security.py).
EXCLUDED_DNS = {"api.nomadkaraoke.com", "api-edge-test.nomadkaraoke.com"}
EXCLUDED_RULESET_PHASES = {
    "nomadkaraoke.com": {"http_request_firewall_custom", "http_ratelimit", "http_request_late_transform"},
    # pulumi-cloudflare 6.22.1 drops set_config bic:false, so refresh fails on this one (see rulesets.py).
    "karaokehunt.com": {"http_config_settings"},
}
# Created/deleted at runtime by gen's backend (backend/services/tenant_domain_service.py):
# every CNAME pointing at the tenant Pages project, and that project's custom domains.
TENANT_PAGES_PROJECT = "karaoke-gen-tenant"
# Owned by karaoke-decide's Pulumi stack.
EXCLUDED_WORKER_ROUTES = {"decide.nomadkaraoke.com/api/*"}
# Settings we pin explicitly (everything else stays at Cloudflare's defaults).
MANAGED_ZONE_SETTINGS = ["ssl"]


def cf(path):
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        body = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
    if not body.get("success", True):
        sys.exit(f"{path}: {body.get('errors')}")
    return body["result"]


def slug(*parts):
    s = "-".join(str(p) for p in parts if p not in (None, ""))
    return re.sub(r"[^a-zA-Z0-9-]+", "-", s.lower()).strip("-")


def main():
    out, seen = [], set()

    def add(typ, name, rid):
        n, i = name, 2
        while n in seen:  # duplicate names (e.g. several MX/TXT at the apex) get a suffix
            n, i = f"{name}-{i}", i + 1
        seen.add(n)
        out.append({"type": f"cloudflare:index/{typ}", "name": n, "id": rid})

    zones = cf(f"/zones?account.id={ACCOUNT}&per_page=50")
    tenant_target = None
    for p in cf(f"/accounts/{ACCOUNT}/pages/projects"):
        if p["name"] == TENANT_PAGES_PROJECT:
            tenant_target = p["subdomain"]

    for z in sorted(zones, key=lambda z: z["name"]):
        zn, zid = z["name"], z["id"]
        zs = slug(zn)
        add("zone:Zone", f"zone-{zs}", zid)
        for s in MANAGED_ZONE_SETTINGS:
            add("zoneSetting:ZoneSetting", f"setting-{zs}-{s}", f"{zid}/{s}")

        for r in sorted(cf(f"/zones/{zid}/dns_records?per_page=500"),
                        key=lambda r: (r["name"], r["type"], str(r.get("content")))):
            if (r.get("meta") or {}).get("read_only"):
                continue  # owned by Email Routing / Workers custom domains
            if r["name"] in EXCLUDED_DNS:
                continue
            if tenant_target and r["type"] == "CNAME" and r.get("content") == tenant_target:
                continue
            label = r["name"][: -len(zn)].rstrip(".") or "apex"
            add("dnsRecord:DnsRecord", f"dns-{zs}-{slug(label)}-{r['type'].lower()}", f"{zid}/{r['id']}")

        for rs in cf(f"/zones/{zid}/rulesets"):
            if rs.get("kind") != "zone" or rs["phase"] in EXCLUDED_RULESET_PHASES.get(zn, set()):
                continue
            add("ruleset:Ruleset", f"ruleset-{zs}-{slug(rs['phase'].removeprefix('http_'))}",
                f"zones/{zid}/{rs['id']}")

        routing = cf(f"/zones/{zid}/email/routing")
        if routing.get("enabled"):
            add("emailRoutingSettings:EmailRoutingSettings", f"email-routing-{zs}", zid)
            add("emailRoutingCatchAll:EmailRoutingCatchAll", f"email-catchall-{zs}", zid)
            for rule in cf(f"/zones/{zid}/email/routing/rules"):
                if any(m.get("type") == "all" for m in rule.get("matchers", [])):
                    continue  # the catch-all, handled above
                to = next((m.get("value") for m in rule["matchers"] if m.get("field") == "to"), rule["tag"])
                add("emailRoutingRule:EmailRoutingRule", f"email-rule-{slug(to)}", f"{zid}/{rule['tag']}")

        for route in cf(f"/zones/{zid}/workers/routes"):
            if route["pattern"] in EXCLUDED_WORKER_ROUTES:
                continue
            add("workersRoute:WorkersRoute", f"worker-route-{slug(route['pattern'])}", f"{zid}/{route['id']}")

    for a in cf(f"/accounts/{ACCOUNT}/email/routing/addresses"):
        add("emailRoutingAddress:EmailRoutingAddress", f"email-dest-{slug(a['email'])}", f"{ACCOUNT}/{a['id']}")

    for t in cf(f"/accounts/{ACCOUNT}/cfd_tunnel?is_deleted=false"):
        add("zeroTrustTunnelCloudflared:ZeroTrustTunnelCloudflared", f"tunnel-{slug(t['name'])}", f"{ACCOUNT}/{t['id']}")
        add("zeroTrustTunnelCloudflaredConfig:ZeroTrustTunnelCloudflaredConfig",
            f"tunnel-config-{slug(t['name'])}", f"{ACCOUNT}/{t['id']}")

    add("zeroTrustOrganization:ZeroTrustOrganization", "access-organization", ACCOUNT)
    for idp in cf(f"/accounts/{ACCOUNT}/access/identity_providers"):
        if idp["type"] == "cloudflare":
            continue  # built-in, not user-managed
        add("zeroTrustAccessIdentityProvider:ZeroTrustAccessIdentityProvider",
            f"access-idp-{slug(idp['type'])}", f"accounts/{ACCOUNT}/{idp['id']}")
    for tok in cf(f"/accounts/{ACCOUNT}/access/service_tokens"):
        add("zeroTrustAccessServiceToken:ZeroTrustAccessServiceToken",
            f"access-service-token-{slug(tok['name'])}", f"accounts/{ACCOUNT}/{tok['id']}")
    for app in cf(f"/accounts/{ACCOUNT}/access/apps"):
        add("zeroTrustAccessApplication:ZeroTrustAccessApplication",
            f"access-app-{slug(app['domain'])}", f"accounts/{ACCOUNT}/{app['id']}")

    for p in cf(f"/accounts/{ACCOUNT}/pages/projects"):
        add("pagesProject:PagesProject", f"pages-{slug(p['name'])}", f"{ACCOUNT}/{p['name']}")
        if p["name"] == TENANT_PAGES_PROJECT:
            continue  # tenant custom domains are managed at runtime by gen's backend
        for d in p.get("domains", []):
            if d.endswith(".pages.dev"):
                continue
            add("pagesDomain:PagesDomain", f"pages-domain-{slug(d)}", f"{ACCOUNT}/{p['name']}/{d}")

    for d in cf(f"/accounts/{ACCOUNT}/workers/domains"):
        add("workersCustomDomain:WorkersCustomDomain", f"worker-domain-{slug(d['hostname'])}", f"{ACCOUNT}/{d['id']}")

    json.dump({"resources": out}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
