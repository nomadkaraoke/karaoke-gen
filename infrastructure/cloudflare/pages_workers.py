"""Pages projects + non-tenant custom domains, Worker custom domains and routes. Code deploys stay with
each repo (gen CI wrangler, public-website Workers Builds); karaoke-decide's route lives in decide's stack.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS

worker_route_create_karaokehunt_com = cloudflare.WorkersRoute("worker-route-create-karaokehunt-com",
    pattern="create.karaokehunt.com/*",
    script="karaokehunt-interceptor",
    zone_id=ZONE_IDS["karaokehunt.com"])
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
