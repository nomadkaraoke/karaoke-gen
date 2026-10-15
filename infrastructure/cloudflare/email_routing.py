"""Email Routing: destination address, per-zone enablement (protected), catch-all and literal rules.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS

email_routing_karaokehunt_com = cloudflare.EmailRoutingSettings("email-routing-karaokehunt-com", zone_id=ZONE_IDS["karaokehunt.com"],
    support_subaddress=False,  # explicit, else the provider plans a no-op update every run
    opts=pulumi.ResourceOptions(protect=True))
email_catchall_karaokehunt_com = cloudflare.EmailRoutingCatchAll("email-catchall-karaokehunt-com",
    actions=[{
        "type": "forward",
        "values": ["andrew.d.beveridge@gmail.com"],
    }],
    enabled=True,
    matchers=[{
        "type": "all",
    }],
    source="api",
    zone_id=ZONE_IDS["karaokehunt.com"])
email_rule_google_karaokehunt_com = cloudflare.EmailRoutingRule("email-rule-google-karaokehunt-com",
    actions=[{
        "type": "forward",
        "values": ["andrew.d.beveridge@gmail.com"],
    }],
    enabled=True,
    matchers=[{
        "field": "to",
        "type": "literal",
        "value": "google@karaokehunt.com",
    }],
    name="google@ forward",
    source="api",
    zone_id=ZONE_IDS["karaokehunt.com"])
email_routing_nomadkaraoke_com = cloudflare.EmailRoutingSettings("email-routing-nomadkaraoke-com", zone_id=ZONE_IDS["nomadkaraoke.com"],
    support_subaddress=False,  # explicit, else the provider plans a no-op update every run
    opts=pulumi.ResourceOptions(protect=True))
email_catchall_nomadkaraoke_com = cloudflare.EmailRoutingCatchAll("email-catchall-nomadkaraoke-com",
    actions=[{
        "type": "forward",
        "values": ["andrew.d.beveridge@gmail.com"],
    }],
    enabled=True,
    matchers=[{
        "type": "all",
    }],
    source="api",
    zone_id=ZONE_IDS["nomadkaraoke.com"])
email_rule_google_nomadkaraoke_com = cloudflare.EmailRoutingRule("email-rule-google-nomadkaraoke-com",
    actions=[{
        "type": "forward",
        "values": ["andrew.d.beveridge@gmail.com"],
    }],
    enabled=True,
    matchers=[{
        "field": "to",
        "type": "literal",
        "value": "google@nomadkaraoke.com",
    }],
    name="google@ forward",
    source="api",
    zone_id=ZONE_IDS["nomadkaraoke.com"])
email_routing_nomadkaroke_com = cloudflare.EmailRoutingSettings("email-routing-nomadkaroke-com", zone_id=ZONE_IDS["nomadkaroke.com"],
    support_subaddress=False,  # explicit, else the provider plans a no-op update every run
    opts=pulumi.ResourceOptions(protect=True))
email_catchall_nomadkaroke_com = cloudflare.EmailRoutingCatchAll("email-catchall-nomadkaroke-com",
    actions=[{
        "type": "forward",
        "values": ["andrew.d.beveridge@gmail.com"],
    }],
    enabled=True,
    matchers=[{
        "type": "all",
    }],
    source="api",
    zone_id=ZONE_IDS["nomadkaroke.com"])
email_rule_google_nomadkaroke_com = cloudflare.EmailRoutingRule("email-rule-google-nomadkaroke-com",
    actions=[{
        "type": "forward",
        "values": ["andrew.d.beveridge@gmail.com"],
    }],
    enabled=True,
    matchers=[{
        "field": "to",
        "type": "literal",
        "value": "google@nomadkaroke.com",
    }],
    name="google@ forward",
    source="api",
    zone_id=ZONE_IDS["nomadkaroke.com"])
email_dest_andrew_d_beveridge_gmail_com = cloudflare.EmailRoutingAddress("email-dest-andrew-d-beveridge-gmail-com",
    account_id=ACCOUNT_ID,
    email="andrew.d.beveridge@gmail.com")
