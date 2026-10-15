"""Zones (protected) and the zone settings we pin explicitly.

Adopted from the live account with `pulumi import` (2026-10-15); see README.md.
"""
import pulumi
import pulumi_cloudflare as cloudflare

from ids import ACCOUNT_ID, ZONE_IDS

zone_karaokehunt_com = cloudflare.Zone("zone-karaokehunt-com",
    account={
        "id": ACCOUNT_ID,
    },
    name="karaokehunt.com",
    type="full",
    opts=pulumi.ResourceOptions(protect=True))
setting_karaokehunt_com_ssl = cloudflare.ZoneSetting("setting-karaokehunt-com-ssl",
    setting_id="ssl",
    value="strict",
    zone_id=ZONE_IDS["karaokehunt.com"])
zone_kjbox_cc = cloudflare.Zone("zone-kjbox-cc",
    account={
        "id": ACCOUNT_ID,
    },
    name="kjbox.cc",
    type="full",
    opts=pulumi.ResourceOptions(protect=True))
setting_kjbox_cc_ssl = cloudflare.ZoneSetting("setting-kjbox-cc-ssl",
    setting_id="ssl",
    value="full",
    zone_id=ZONE_IDS["kjbox.cc"])
zone_kjtip_me = cloudflare.Zone("zone-kjtip-me",
    account={
        "id": ACCOUNT_ID,
    },
    name="kjtip.me",
    type="full",
    opts=pulumi.ResourceOptions(protect=True))
setting_kjtip_me_ssl = cloudflare.ZoneSetting("setting-kjtip-me-ssl",
    setting_id="ssl",
    value="full",
    zone_id=ZONE_IDS["kjtip.me"])
zone_nomadkaraoke_com = cloudflare.Zone("zone-nomadkaraoke-com",
    account={
        "id": ACCOUNT_ID,
    },
    name="nomadkaraoke.com",
    type="full",
    opts=pulumi.ResourceOptions(protect=True))
setting_nomadkaraoke_com_ssl = cloudflare.ZoneSetting("setting-nomadkaraoke-com-ssl",
    setting_id="ssl",
    value="full",
    zone_id=ZONE_IDS["nomadkaraoke.com"])
zone_nomadkaroke_com = cloudflare.Zone("zone-nomadkaroke-com",
    account={
        "id": ACCOUNT_ID,
    },
    name="nomadkaroke.com",
    type="full",
    opts=pulumi.ResourceOptions(protect=True))
setting_nomadkaroke_com_ssl = cloudflare.ZoneSetting("setting-nomadkaroke-com-ssl",
    setting_id="ssl",
    value="flexible",
    zone_id=ZONE_IDS["nomadkaroke.com"])
