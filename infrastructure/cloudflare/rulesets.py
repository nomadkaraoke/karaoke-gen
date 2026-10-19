"""Zone ruleset entrypoints (one owner per zone+phase). nomadkaraoke.com's http_request_firewall_custom,
http_ratelimit and http_request_late_transform phases are owned by gen's GCP stack (modules/edge_security.py).

Not managed here: karaokehunt.com's http_config_settings phase ("SSL for create.karaokehunt.com" and
"Disable challenge for create API" = set_config bic:false). pulumi-cloudflare 6.22.1 drops the `false`,
so refresh fails with "action parameters are required for the set_config action". Edit it in the dashboard.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS

ruleset_karaokehunt_com_request_dynamic_redirect = cloudflare.Ruleset("ruleset-karaokehunt-com-request-dynamic-redirect",
    kind="zone",
    name="default",
    phase="http_request_dynamic_redirect",
    rules=[
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "status_code": 301,
                    "target_url": {
                        "value": "https://nomadkaraoke.com",
                    },
                },
            },
            "description": "Redirect all to nomadkaraoke.com",
            "enabled": True,
            "expression": "(http.host ne \"create.karaokehunt.com\")",
            "ref": "379a4603c27e4acd9b259b83c4513e7c",
        },
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "status_code": 302,
                    "target_url": {
                        "value": "https://insigh.to/b/karaokehunt",
                    },
                },
            },
            "description": "Public roadmap",
            "enabled": True,
            "expression": "(http.host eq \"roadmap.karaokehunt.com\")",
            "ref": "af4ff6bc30184e2ea8d85c97f075c6f5",
        },
    ],
    zone_id=ZONE_IDS["karaokehunt.com"])
ruleset_karaokehunt_com_request_firewall_custom = cloudflare.Ruleset("ruleset-karaokehunt-com-request-firewall-custom",
    kind="zone",
    name="default",
    phase="http_request_firewall_custom",
    rules=[{
        "action": "block",
        "description": "Block AI Scrapers and Crawlers rule",
        "enabled": True,
        "expression": "(cf.verified_bot_category eq \"AI Crawler\")",
        "ref": "a3152fbf8921417caa5e70021fd37c9a",
    }],
    zone_id=ZONE_IDS["karaokehunt.com"])
ruleset_kjbox_cc_request_dynamic_redirect = cloudflare.Ruleset("ruleset-kjbox-cc-request-dynamic-redirect",
    description="kjbox.cc short links -> sing.nomadkaraoke.com",
    kind="zone",
    name="default",
    phase="http_request_dynamic_redirect",
    rules=[
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "preserve_query_string": True,
                    "status_code": 302,
                    "target_url": {
                        "value": "https://sing.nomadkaraoke.com/",
                    },
                },
            },
            "description": "kjbox.cc/ -> singer code-entry page",
            "enabled": True,
            "expression": "(http.host in {\"kjbox.cc\" \"www.kjbox.cc\"} and http.request.uri.path eq \"/\")",
            "ref": "62596d3873e04b2db1e802d92c966005",
        },
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "status_code": 302,
                    "target_url": {
                        "expression": "wildcard_replace(http.request.uri.path, r\"/*\", r\"https://sing.nomadkaraoke.com/?t=${1}\")",
                    },
                },
            },
            "description": "kjbox.cc/<code> -> sing.nomadkaraoke.com/?t=<code>",
            "enabled": True,
            "expression": "(http.host in {\"kjbox.cc\" \"www.kjbox.cc\"} and http.request.uri.path ne \"/\")",
            "ref": "1f2e8fb05f844449ae29e7c1237ffc5c",
        },
    ],
    zone_id=ZONE_IDS["kjbox.cc"])
ruleset_kjtip_me_request_dynamic_redirect = cloudflare.Ruleset("ruleset-kjtip-me-request-dynamic-redirect",
    kind="zone",
    name="default",
    phase="http_request_dynamic_redirect",
    rules=[{
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "status_code": 301,
                "target_url": {
                    "value": "https://nomadkaraoke.com/tip",
                },
            },
        },
        "description": "kjtip.me → nomadkaraoke.com/tip (KJ tip shortlink)",
        "enabled": True,
        "expression": "true",
        "ref": "8835998667c947fea41d01c3c7721a24",
    }],
    zone_id=ZONE_IDS["kjtip.me"])
ruleset_nomadkaraoke_com_request_dynamic_redirect = cloudflare.Ruleset("ruleset-nomadkaraoke-com-request-dynamic-redirect",
    kind="zone",
    name="default",
    phase="http_request_dynamic_redirect",
    rules=[
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "status_code": 301,
                    "target_url": {
                        "expression": "wildcard_replace(http.request.full_uri, r\"http://*\", r\"https://${1}\")",
                    },
                },
            },
            "description": "Redirect from HTTP to HTTPS [Template]",
            "enabled": True,
            "expression": "(http.request.full_uri wildcard r\"http://*\")",
            "ref": "2cd5ecabdaaa41b49820841a0fef485f",
        },
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "status_code": 301,
                    "target_url": {
                        "value": "https://discord.gg/FkrKrSmZEg",
                    },
                },
            },
            "description": "Discord Invite Redirect",
            "enabled": True,
            "expression": "(http.request.full_uri wildcard r\"*discord.nomadkaraoke.com*\")",
            "ref": "4ee3e9d72fc3433e8164a4cacad8a7fd",
        },
        {
            "action": "redirect",
            "action_parameters": {
                "from_value": {
                    "status_code": 301,
                    "target_url": {
                        "value": "https://buy.stripe.com/00geUZgfHdhx6TS001",
                    },
                },
            },
            "description": "Stripe Pay Redirect",
            "enabled": True,
            "expression": "(http.host eq \"pay.nomadkaraoke.com\")",
            "ref": "763ba6175ef94a2faae1c1840eaf5145",
        },
    ],
    zone_id=ZONE_IDS["nomadkaraoke.com"])
ruleset_nomadkaraoke_com_config_settings = cloudflare.Ruleset("ruleset-nomadkaraoke-com-config-settings",
    kind="zone",
    name="default",
    phase="http_config_settings",
    rules=[{
        "action": "set_config",
        "action_parameters": {
            "ssl": "flexible",
        },
        "description": "Flacfetch Origin Flexible SSL",
        "enabled": True,
        "expression": "(http.host eq \"flacfetch.nomadkaraoke.com\")",
        "ref": "7e856f00815a49ec8abb35266fad134d",
    }],
    zone_id=ZONE_IDS["nomadkaraoke.com"])
ruleset_nomadkaraoke_com_request_origin = cloudflare.Ruleset("ruleset-nomadkaraoke-com-request-origin",
    kind="zone",
    name="default",
    phase="http_request_origin",
    rules=[{
        "action": "route",
        "action_parameters": {
            "origin": {
                "port": 8080,
            },
        },
        "description": "Flacfetch Protecc Port 8080",
        "enabled": True,
        "expression": "(http.host eq \"flacfetch.nomadkaraoke.com\")",
        "ref": "95cbc9906fc747ee8bfbd1a57f4e55ca",
    }],
    zone_id=ZONE_IDS["nomadkaraoke.com"])
ruleset_nomadkaroke_com_request_dynamic_redirect = cloudflare.Ruleset("ruleset-nomadkaroke-com-request-dynamic-redirect",
    kind="zone",
    name="default",
    phase="http_request_dynamic_redirect",
    rules=[{
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "preserve_query_string": True,
                "status_code": 301,
                "target_url": {
                    "value": "https://nomadkaraoke.com",
                },
            },
        },
        "description": "Redirect to correct domain",
        "enabled": True,
        "expression": "true",
        "ref": "21a789b686fe4bbd91966ac505cd479c",
    }],
    zone_id=ZONE_IDS["nomadkaroke.com"])
