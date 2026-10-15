"""Remote-managed cloudflared tunnels and their ingress. Connectors: NomadPC systemd `cloudflared-nomad`
(nomad-kjbox) and hf2 `~/nomad-tunnel` compose (nomad-hf2); connector tokens are not managed here.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS

tunnel_nomad_hf2 = cloudflare.ZeroTrustTunnelCloudflared("tunnel-nomad-hf2",
    account_id=ACCOUNT_ID,
    config_src="cloudflare",
    name="nomad-hf2",
    opts=pulumi.ResourceOptions(protect=True))
tunnel_config_nomad_hf2 = cloudflare.ZeroTrustTunnelCloudflaredConfig("tunnel-config-nomad-hf2",
    account_id=ACCOUNT_ID,
    config={
        "ingresses": [
            {
                # fiverrbot delivery approval page (aiohttp in the fiverrbot container);
                # same Access app as the console. Must precede the catch-all host rule.
                "hostname": "fiverrbot.nomadkaraoke.com",
                "path": "^/approve",
                "service": "http://fiverrbot:8088",
            },
            {
                "hostname": "fiverrbot.nomadkaraoke.com",
                "service": "http://fiverrbot:6080",
            },
            {
                "service": "http_status:404",
            },
        ],
    },
    source="cloudflare",
    tunnel_id="531403b1-bcfa-4f6e-912e-a10bd8e5ebd5")
tunnel_nomad_kjbox = cloudflare.ZeroTrustTunnelCloudflared("tunnel-nomad-kjbox",
    account_id=ACCOUNT_ID,
    config_src="cloudflare",
    name="nomad-kjbox",
    opts=pulumi.ResourceOptions(protect=True))
tunnel_config_nomad_kjbox = cloudflare.ZeroTrustTunnelCloudflaredConfig("tunnel-config-nomad-kjbox",
    account_id=ACCOUNT_ID,
    config={
        "ingresses": [
            {
                "hostname": "kjssh.nomadkaraoke.com",
                "service": "ssh://localhost:22",
            },
            {
                "hostname": "kjvnc.nomadkaraoke.com",
                "service": "http://localhost:6080",
            },
            {
                "hostname": "kjbox.nomadkaraoke.com",
                "origin_request": {
                    "no_tls_verify": True,
                },
                "service": "https://localhost:443",
            },
            {
                "hostname": "sing.nomadkaraoke.com",
                "origin_request": {
                    "no_tls_verify": True,
                },
                "service": "https://localhost:443",
            },
            {
                "service": "http_status:404",
            },
        ],
    },
    source="cloudflare",
    tunnel_id="04c88337-f076-45ed-bc19-4ac1222b4228")
