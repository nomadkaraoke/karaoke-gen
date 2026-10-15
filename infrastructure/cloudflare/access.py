"""Zero Trust: organization (protected), identity provider, service token and self-hosted Access apps.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS

access_organization = cloudflare.ZeroTrustOrganization("access-organization",
    account_id=ACCOUNT_ID,
    auth_domain="nomadkaraoke.cloudflareaccess.com",
    name="Nomad Karaoke",
    strict_service_token_auth=True,
    opts=pulumi.ResourceOptions(protect=True))
access_idp_onetimepin = cloudflare.ZeroTrustAccessIdentityProvider("access-idp-onetimepin",
    account_id=ACCOUNT_ID,
    config={},
    name="One-time PIN",
    type="onetimepin")
access_service_token_claude_code_kjbox = cloudflare.ZeroTrustAccessServiceToken("access-service-token-claude-code-kjbox",
    account_id=ACCOUNT_ID,
    duration="forever",
    enabled=True,
    name="claude-code-kjbox")

# Reusable policies (the rules themselves live here, not in the dashboard). session_duration is
# pinned to the apps' 730h because a reusable policy otherwise imposes Cloudflare's 24h default.
policy_kj_controller_users = cloudflare.ZeroTrustAccessPolicy("access-policy-kj-controller-users",
    account_id=ACCOUNT_ID,
    name="KJ Controller: Andrew, David, @nomadkaraoke.com, @beveridge.uk",
    decision="allow",
    session_duration="730h",
    includes=[
        {"email": {"email": "andrew.d.beveridge@gmail.com"}},
        {"email": {"email": "david.t.axe@gmail.com"}},
        {"email_domain": {"domain": "nomadkaraoke.com"}},
        {"email_domain": {"domain": "beveridge.uk"}},
    ])
policy_kj_controller_service_token = cloudflare.ZeroTrustAccessPolicy("access-policy-kj-controller-service-token",
    account_id=ACCOUNT_ID,
    name="KJ Controller: claude-code-kjbox service token",
    decision="non_identity",
    includes=[{"service_token": {"token_id": access_service_token_claude_code_kjbox.id}}])
policy_fiverrbot_users = cloudflare.ZeroTrustAccessPolicy("access-policy-fiverrbot-users",
    account_id=ACCOUNT_ID,
    name="FiverrBot: Andrew only (explicit emails)",
    decision="allow",
    session_duration="730h",
    includes=[
        {"email": {"email": "andrew.d.beveridge@gmail.com"}},
        {"email": {"email": "andrew@beveridge.uk"}},
        {"email": {"email": "admin@nomadkaraoke.com"}},
    ])
access_app_fiverrbot_nomadkaraoke_com = cloudflare.ZeroTrustAccessApplication("access-app-fiverrbot-nomadkaraoke-com",
    account_id=ACCOUNT_ID,
    app_launcher_visible=True,
    destinations=[{
        "type": "public",
        "uri": "fiverrbot.nomadkaraoke.com",
    }],
    domain="fiverrbot.nomadkaraoke.com",
    http_only_cookie_attribute=True,
    name="FiverrBot noVNC",
    policies=[{"id": policy_fiverrbot_users.id, "precedence": 1}],
    session_duration="730h",
    type="self_hosted")
access_app_kjbox_nomadkaraoke_com = cloudflare.ZeroTrustAccessApplication("access-app-kjbox-nomadkaraoke-com",
    account_id=ACCOUNT_ID,
    app_launcher_visible=True,
    destinations=[{
        "type": "public",
        "uri": "kjbox.nomadkaraoke.com",
    }],
    domain="kjbox.nomadkaraoke.com",
    http_only_cookie_attribute=True,
    name="KJ Controller",
    policies=[
        {"id": policy_kj_controller_users.id, "precedence": 1},
        {"id": policy_kj_controller_service_token.id, "precedence": 2},
    ],
    session_duration="730h",
    type="self_hosted")
