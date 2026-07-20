"""
Cloudflare edge security for the karaoke backend.

Puts Cloudflare's WAF, rate limiting and bot mitigation in front of
``api.nomadkaraoke.com`` (Cloud Run) and locks the origin so it only accepts
traffic that came through Cloudflare (via a secret header the edge injects).

WHY THIS EXISTS
    The backend was reachable directly at ``ghs.googlehosted.com`` (Cloud Run
    domain mapping, DNS-only / grey-cloud) with no WAF — a vuln scanner hammered
    it with ``/.env`` / ``/.ssh/id_rsa`` style probes on 2026-07-20. This module
    moves the API behind the Cloudflare edge that already fronts the rest of the
    domain.

ZERO-DOWNTIME ROLLOUT (see docs/archive/2026-07-20-edge-security-hardening-plan.md)
    Everything here is driven by ``CloudflareConfig.rollout_stage()``:

      stage="staging" (default, NON-disruptive):
        * adds a *second* Cloud Run domain mapping + DNS record for
          ``api-edge-test.nomadkaraoke.com`` (proxied) → same karaoke-backend
        * edge rules (WAF / rate-limit / header inject) are scoped to the
          staging host ONLY
        * the prod ``api`` record is left exactly as today (DNS-only)
        → we validate the whole Cloud-Run-×-Cloudflare interaction (incl. the
          managed-cert / SSL "dragon") without any prod impact.

      stage="cutover" (the single prod-affecting change, instantly reversible):
        * the prod ``api`` record flips to proxied=True
        * edge rules apply to the prod host too
        → rollback = set stage back to "staging" (or proxied=False) + `pulumi up`.

PROVIDER
    pulumi-cloudflare 6.x (see infrastructure/requirements.txt). Resource arg
    shapes were introspected against 6.18.0. Run ``pulumi preview`` after any
    provider bump — Cloudflare restructures ruleset schemas between majors.

CREDENTIALS / CONFIG (per stack, not committed)
    pulumi config set --secret cloudflare:apiToken <token>   # Zone WAF:Edit + DNS:Edit + Zone Settings:Edit
    pulumi config set edge:cloudflareZoneId    <zone id for nomadkaraoke.com>
    pulumi config set edge:cloudflareAccountId <account id>
    pulumi config set edge:rolloutStage        staging   # then later: cutover
"""

from typing import Optional

import pulumi
import pulumi_cloudflare as cloudflare
import pulumi_gcp as gcp
from pulumi_gcp import cloudrun

from config import CloudflareConfig, REGION, PROJECT_ID


# Cloudflare's account-level "Cloudflare Managed Ruleset" (OWASP-style core
# protections). This id is stable across all Cloudflare accounts.
CLOUDFLARE_MANAGED_RULESET_ID = "efb7b8c949ac4650a09736fc376e9aee"

# Regex of exploit / secret-exposure paths to hard-block at the edge regardless
# of source. Matches the 2026-07-20 scanner signature and common web probes.
EXPLOIT_PATH_REGEX = (
    r"(?i)(^|/)(\.env|\.git/|\.aws/|\.ssh/|\.mysql_history|\.bash_history|"
    r"wp-config\.php|wp-login\.php|wp-admin|configuration\.php|"
    r"etc/(passwd|shadow|hosts)|proc/(self|net)|var/log/)"
)


def _hosts_for_stage() -> list[str]:
    """Which hostnames the edge rules apply to, per rollout stage."""
    if CloudflareConfig.rollout_stage() == "cutover":
        return [CloudflareConfig.PROD_API_HOST, CloudflareConfig.STAGING_API_HOST]
    return [CloudflareConfig.STAGING_API_HOST]


def _host_match_expression(hosts: list[str]) -> str:
    """Cloudflare filter expression matching any of the given hosts."""
    quoted = " ".join(f'"{h}"' for h in hosts)
    return f"(http.host in {{{quoted}}})"


# --------------------------------------------------------------------------- #
# Staging Cloud Run domain mapping (same service as prod api)
# --------------------------------------------------------------------------- #
def create_staging_domain_mapping() -> cloudrun.DomainMapping:
    """
    Second Cloud Run domain mapping so ``api-edge-test.nomadkaraoke.com`` serves
    the SAME karaoke-backend service as prod. Lets us validate the full edge
    stack end-to-end without touching the live ``api`` mapping. Torn down after
    cutover (delete this resource + its DNS record, then `pulumi up`).
    """
    return cloudrun.DomainMapping(
        "karaoke-backend-edge-test-domain",
        location=REGION,
        name=CloudflareConfig.STAGING_API_HOST,
        metadata=cloudrun.DomainMappingMetadataArgs(namespace=PROJECT_ID),
        spec=cloudrun.DomainMappingSpecArgs(
            route_name=CloudflareConfig.DOMAIN_MAPPING_ROUTE,
        ),
    )


# --------------------------------------------------------------------------- #
# DNS records (Cloudflare)
# --------------------------------------------------------------------------- #
def create_dns_records(zone_id: str) -> dict[str, cloudflare.DnsRecord]:
    """
    Manage the ``api`` (prod) and ``api-edge-test`` (staging) CNAMEs.

    - staging: always proxied=True (that's the whole point — test the proxy).
    - prod ``api``: proxied only once rollout_stage == "cutover"; until then it
      stays DNS-only, byte-for-byte the behaviour we have today.

    NOTE: ``ttl`` must be 1 (automatic) whenever proxied=True — Cloudflare
    rejects an explicit TTL on a proxied record.
    """
    records: dict[str, cloudflare.DnsRecord] = {}
    cutover = CloudflareConfig.rollout_stage() == "cutover"

    records["staging"] = cloudflare.DnsRecord(
        "api-edge-test-dns",
        zone_id=zone_id,
        name="api-edge-test",
        type="CNAME",
        content=CloudflareConfig.ORIGIN_CNAME_TARGET,
        ttl=1,
        proxied=True,
        comment="Edge-security staging host → karaoke-backend (throwaway; see edge_security.py)",
    )

    # The prod `api` record ALREADY EXISTS in Cloudflare (id
    # 6c31cba0080ff334a85cfff6c2927219, ttl=1/auto, proxied=False). Pulumi must
    # IMPORT it into this resource before the first apply, otherwise it tries to
    # create a duplicate / errors. Runbook has the import command. ttl is kept at
    # 1 (auto) to match the live record and because a proxied record requires it.
    records["prod"] = cloudflare.DnsRecord(
        "api-dns",
        zone_id=zone_id,
        name="api",
        type="CNAME",
        content=CloudflareConfig.ORIGIN_CNAME_TARGET,
        ttl=1,
        proxied=cutover,
        comment="karaoke-backend (Cloud Run). Proxied via Cloudflare at cutover.",
    )

    return records


# --------------------------------------------------------------------------- #
# WAF: custom exploit-path block + Cloudflare Managed Ruleset
# --------------------------------------------------------------------------- #
def create_waf_rulesets(zone_id: str, hosts: list[str]) -> dict[str, cloudflare.Ruleset]:
    host_expr = _host_match_expression(hosts)
    rulesets: dict[str, cloudflare.Ruleset] = {}

    # Custom firewall rules (evaluated before managed rules).
    rulesets["custom"] = cloudflare.Ruleset(
        "edge-waf-custom",
        zone_id=zone_id,
        name="karaoke-backend edge WAF (custom)",
        description="Block secret-exposure / exploit path probes at the edge.",
        kind="zone",
        phase="http_request_firewall_custom",
        rules=[
            cloudflare.RulesetRuleArgs(
                action="block",
                description="Block exploit / secret-exposure paths",
                expression=(
                    f'{host_expr} and '
                    f'(http.request.uri.path matches "{EXPLOIT_PATH_REGEX}")'
                ),
                enabled=True,
            ),
        ],
    )

    # Cloudflare Managed Ruleset (OWASP-style). Only available on Pro+ plans —
    # the zone is currently Free, so this is gated off by default (see
    # CloudflareConfig.managed_waf_enabled). Deploy in LOG mode first so we can
    # review false positives before switching the override action to "block".
    if CloudflareConfig.managed_waf_enabled():
        _managed_action = "log"
        rulesets["managed"] = cloudflare.Ruleset(
            "edge-waf-managed",
            zone_id=zone_id,
            name="karaoke-backend edge WAF (managed)",
            description="Cloudflare Managed Ruleset (log first, then block).",
            kind="zone",
            phase="http_request_firewall_managed",
            rules=[
                cloudflare.RulesetRuleArgs(
                    action="execute",
                    description="Execute Cloudflare Managed Ruleset",
                    expression=host_expr,
                    enabled=True,
                    action_parameters=cloudflare.RulesetRuleActionParametersArgs(
                        id=CLOUDFLARE_MANAGED_RULESET_ID,
                        overrides=cloudflare.RulesetRuleActionParametersOverridesArgs(
                            action=_managed_action,
                        ),
                    ),
                ),
            ],
        )

    return rulesets


# --------------------------------------------------------------------------- #
# Rate limiting (per-IP), excluding scheduler cron paths
# --------------------------------------------------------------------------- #
def create_rate_limit_ruleset(zone_id: str, hosts: list[str]) -> cloudflare.Ruleset:
    host_expr = _host_match_expression(hosts)
    # Don't rate-limit OIDC-authed scheduler hits to /api/internal/*.
    expression = (
        f'{host_expr} and '
        f'(not starts_with(http.request.uri.path, "{CloudflareConfig.INTERNAL_PATH_PREFIX}"))'
    )
    return cloudflare.Ruleset(
        "edge-ratelimit",
        zone_id=zone_id,
        name="karaoke-backend edge rate limit",
        description="Throttle abusive clients by IP; exclude internal cron paths.",
        kind="zone",
        phase="http_ratelimit",
        rules=[
            cloudflare.RulesetRuleArgs(
                action="block",
                description=(
                    f"Block >{CloudflareConfig.RATE_LIMIT_REQUESTS} req / "
                    f"{CloudflareConfig.RATE_LIMIT_PERIOD_SECONDS}s per IP"
                ),
                expression=expression,
                enabled=True,
                ratelimit=cloudflare.RulesetRuleRatelimitArgs(
                    characteristics=["ip.src", "cf.colo.id"],
                    period=CloudflareConfig.RATE_LIMIT_PERIOD_SECONDS,
                    requests_per_period=CloudflareConfig.RATE_LIMIT_REQUESTS,
                    mitigation_timeout=CloudflareConfig.RATE_LIMIT_MITIGATION_SECONDS,
                ),
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Origin lockdown: inject a secret header on every proxied request
# --------------------------------------------------------------------------- #
def create_origin_header_transform(
    zone_id: str, hosts: list[str], secret: pulumi.Input[str]
) -> cloudflare.Ruleset:
    """
    Add ``X-Edge-Auth: <secret>`` to requests as they leave the Cloudflare edge
    for the origin. The backend edge-auth middleware rejects public requests
    that lack it — so anyone hitting the raw ``*.run.app`` / ghs origin directly
    (bypassing Cloudflare) is refused. A Cloud Run domain mapping can't use
    ingress=internal, so this app-layer check is how we lock the origin.
    """
    host_expr = _host_match_expression(hosts)
    return cloudflare.Ruleset(
        "edge-origin-header",
        zone_id=zone_id,
        name="karaoke-backend origin auth header",
        description="Inject shared-secret header so origin can reject non-edge traffic.",
        kind="zone",
        phase="http_request_late_transform",
        rules=[
            cloudflare.RulesetRuleArgs(
                action="rewrite",
                description="Set origin auth header",
                expression=host_expr,
                enabled=True,
                action_parameters=cloudflare.RulesetRuleActionParametersArgs(
                    headers={
                        CloudflareConfig.ORIGIN_AUTH_HEADER: cloudflare.RulesetRuleActionParametersHeadersArgs(
                            operation="set",
                            value=secret,
                        ),
                    },
                ),
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Zone-level bot mitigation / security level
# --------------------------------------------------------------------------- #
def create_zone_security_settings(zone_id: str) -> dict[str, cloudflare.ZoneSetting]:
    """
    Raise baseline zone protections. These are zone-wide (Cloudflare doesn't
    scope Security Level / Bot Fight Mode per-host), so they also benefit the
    marketing site and other subdomains.
    """
    settings: dict[str, cloudflare.ZoneSetting] = {}
    settings["security_level"] = cloudflare.ZoneSetting(
        "zone-security-level",
        zone_id=zone_id,
        setting_id="security_level",
        value="medium",
    )
    return settings


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def configure_edge_security() -> dict:
    """
    Wire up the whole Cloudflare edge for the backend, honouring the rollout
    stage. Returns a dict of created resources for export/inspection.

    Safe no-op until explicitly enabled: returns ``{}`` and creates nothing
    unless ``edge:enabled`` is True. This MUST gate on the opt-in flag (not the
    zone id, which has a default) so that merging the module before a
    WAF/DNS-scoped ``cloudflare:apiToken`` exists can't make `pulumi up` try to
    create Cloudflare resources with an unauthorized token.

    The origin-lock header value comes from the Pulumi secret config
    ``edge:originSecret`` (same string stored in Secret Manager as
    ``edge-origin-secret`` for the backend to read). If it isn't set, the WAF /
    rate-limit / DNS resources are still created but the origin-header transform
    is skipped (so the backend can stay in EDGE_AUTH_MODE=off/warn safely).
    """
    if not CloudflareConfig.enabled():
        pulumi.log.info(
            "edge_security: edge:enabled is false — Cloudflare edge resources "
            "not created. Set a WAF/DNS-scoped cloudflare:apiToken, then "
            "`pulumi config set edge:enabled true` to activate."
        )
        return {}

    zone_id = CloudflareConfig.zone_id()
    hosts = _hosts_for_stage()
    resources: dict = {"stage": CloudflareConfig.rollout_stage(), "hosts": hosts}

    # Staging domain mapping only needs to exist while we're validating; keep it
    # through cutover for soak, remove later.
    resources["staging_domain_mapping"] = create_staging_domain_mapping()
    resources["dns"] = create_dns_records(zone_id)
    resources["waf"] = create_waf_rulesets(zone_id, hosts)
    resources["rate_limit"] = create_rate_limit_ruleset(zone_id, hosts)
    resources["zone_settings"] = create_zone_security_settings(zone_id)

    origin_secret = pulumi.Config("edge").get_secret("originSecret")
    if origin_secret is not None:
        resources["origin_header"] = create_origin_header_transform(
            zone_id, hosts, origin_secret
        )
    else:
        pulumi.log.warn(
            "edge_security: edge:originSecret not set — origin-header transform "
            "skipped. Keep backend EDGE_AUTH_MODE=off/warn until it's set."
        )

    return resources
