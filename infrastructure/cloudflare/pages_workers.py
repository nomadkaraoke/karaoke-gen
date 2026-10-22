"""Pages projects + non-tenant custom domains, Worker custom domains and routes. Code deploys stay with
each repo (gen CI wrangler, public-website Workers Builds); karaoke-decide's route lives in decide's stack.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS
from well_known import ROUTE_PATTERNS, build_worker_script

worker_route_create_karaokehunt_com = cloudflare.WorkersRoute("worker-route-create-karaokehunt-com",
    pattern="create.karaokehunt.com/*",
    script="karaokehunt-interceptor",
    zone_id=ZONE_IDS["karaokehunt.com"])
# Serves the voting board on requests.nomadkaraoke.com itself (script: requests-host/, deploy.sh).
worker_route_requests_nomadkaraoke_com = cloudflare.WorkersRoute("worker-route-requests-nomadkaraoke-com",
    pattern="requests.nomadkaraoke.com/*",
    script="requests-host",
    zone_id=ZONE_IDS["nomadkaraoke.com"])
pages_karaoke_gen_tenant = cloudflare.PagesProject("pages-karaoke-gen-tenant",
    account_id=ACCOUNT_ID,
    build_config={
        "destination_dir": "out",
    },
    deployment_configs={
        "preview": {
            "build_image_major_version": 3,
            "compatibility_date": "2026-03-03",
            "fail_open": True,
            "usage_model": "standard",
        },
        "production": {
            "build_image_major_version": 3,
            "compatibility_date": "2026-03-03",
            "fail_open": True,
            "usage_model": "standard",
            "wrangler_config_hash": "ff1efaa0ae71bb4a166ec448ed5ced3225ec3ccd9bfdb4ecc88e143b95806b29",
        },
    },
    name="karaoke-gen-tenant",
    production_branch="main")
pages_karaoke_gen = cloudflare.PagesProject("pages-karaoke-gen",
    account_id=ACCOUNT_ID,
    deployment_configs={
        "preview": {
            "build_image_major_version": 3,
            "compatibility_date": "2026-03-03",
            "fail_open": True,
            "usage_model": "standard",
        },
        "production": {
            "build_image_major_version": 3,
            "compatibility_date": "2026-03-03",
            "fail_open": True,
            "usage_model": "standard",
        },
    },
    name="karaoke-gen",
    production_branch="main")
pages_domain_gen_nomadkaraoke_com = cloudflare.PagesDomain("pages-domain-gen-nomadkaraoke-com",
    account_id=ACCOUNT_ID,
    name="gen.nomadkaraoke.com",
    project_name="karaoke-gen")
worker_domain_nomadkaraoke_com = cloudflare.WorkersCustomDomain("worker-domain-nomadkaraoke-com",
    account_id=ACCOUNT_ID,
    environment="production",
    hostname="nomadkaraoke.com",
    service="nomadkaraoke-com-static",
    zone_id=ZONE_IDS["nomadkaraoke.com"],
    zone_name="nomadkaraoke.com")
worker_domain_www_nomadkaraoke_com = cloudflare.WorkersCustomDomain("worker-domain-www-nomadkaraoke-com",
    account_id=ACCOUNT_ID,
    environment="production",
    hostname="www.nomadkaraoke.com",
    service="nomadkaraoke-com-static",
    zone_id=ZONE_IDS["nomadkaraoke.com"],
    zone_name="nomadkaraoke.com")

# kjbox.cc/.well-known/*: Android App Links (assetlinks.json) + iOS universal links (AASA) for the
# Nomad Karaoke app, so the venue QR (kjbox.cc/<code>) can open the app. The script is deployed by
# Pulumi (fingerprints come from config `kjboxcc_android_sha256_fingerprints`, empty by default);
# the kjbox.cc/<code> redirect rule in rulesets.py excludes /.well-known/ so this route answers.
_kjbox_fingerprints = pulumi.Config().get_object("kjboxcc_android_sha256_fingerprints") or []
worker_script_kjbox_cc_well_known = cloudflare.WorkersScript("worker-script-kjbox-cc-well-known",
    account_id=ACCOUNT_ID,
    script_name="kjbox-cc-well-known",
    content=build_worker_script(_kjbox_fingerprints),
    main_module="worker.js",
    compatibility_date="2026-09-01")
for _name, _pattern in ROUTE_PATTERNS.items():
    cloudflare.WorkersRoute(f"worker-route-{_name}-well-known",
        pattern=_pattern,
        script=worker_script_kjbox_cc_well_known.script_name,
        zone_id=ZONE_IDS["kjbox.cc"])
