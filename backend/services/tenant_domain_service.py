"""
Cloudflare provisioning for tenant portal subdomains.

Every white-label tenant is served at ``{tenant_id}.nomadkaraoke.com`` by the
shared ``karaoke-gen-tenant`` Cloudflare Pages project (its edge function reads
the tenant id from the first hostname label). Cloudflare Pages has no wildcard
custom domains, so each tenant needs two Cloudflare objects:

1. a Pages custom domain on ``karaoke-gen-tenant`` (Cloudflare issues the cert), and
2. a proxied ``CNAME {tenant_id} -> karaoke-gen-tenant.pages.dev`` in the
   ``nomadkaraoke.com`` zone.

Both operations are idempotent. We never take over or delete a DNS record that
points anywhere other than the tenant Pages project, so a tenant id can't hijack
a real subdomain (e.g. ``decide``) and deleting a tenant can't remove one.

Auth: ``cloudflare-tenant-domains-token`` secret (account-owned token scoped to
Pages Read/Write on the account + DNS Read/Write on the nomadkaraoke.com zone).
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)

CF_API = "https://api.cloudflare.com/client/v4"
CF_TOKEN_SECRET = "cloudflare-tenant-domains-token"
# Non-secret identifiers (overridable for tests / other environments).
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "a7dd2a2bee7151ef4dc7a9f53d99b520")
CF_ZONE_ID = os.getenv("CLOUDFLARE_NOMADKARAOKE_ZONE_ID", "807f07f458f9cd38251f3b7948d55172")
PAGES_PROJECT = os.getenv("TENANT_PAGES_PROJECT", "karaoke-gen-tenant")
PAGES_TARGET = f"{PAGES_PROJECT}.pages.dev"
BASE_DOMAIN = "nomadkaraoke.com"


class TenantDomainError(RuntimeError):
    """Cloudflare provisioning failed (API error, missing token, ...)."""


class TenantDomainConflictError(TenantDomainError):
    """The hostname already has a DNS record we don't own."""


@dataclass
class DomainStatus:
    """Provisioning state of one tenant hostname."""

    hostname: str
    dns_ok: bool
    pages_status: Optional[str]  # "active", "initializing", "pending", ... or None if absent

    @property
    def active(self) -> bool:
        return self.dns_ok and self.pages_status == "active"

    def to_dict(self) -> Dict[str, Any]:
        if self.active:
            state = "active"
        elif self.dns_ok or self.pages_status:
            state = "provisioning"
        else:
            state = "missing"
        return {
            "hostname": self.hostname,
            "state": state,
            "dns_ok": self.dns_ok,
            "pages_status": self.pages_status,
        }


class TenantDomainService:
    def __init__(
        self,
        api_token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        account_id: str = CF_ACCOUNT_ID,
        zone_id: str = CF_ZONE_ID,
        pages_project: str = PAGES_PROJECT,
    ):
        self._api_token = api_token
        self._client = client
        self.account_id = account_id
        self.zone_id = zone_id
        self.pages_project = pages_project
        self.pages_target = f"{pages_project}.pages.dev"

    # --- HTTP plumbing -------------------------------------------------------

    def _token(self) -> str:
        if not self._api_token:
            self._api_token = get_settings().get_secret(CF_TOKEN_SECRET)
        if not self._api_token:
            raise TenantDomainError(
                f"Cloudflare token secret '{CF_TOKEN_SECRET}' is not configured; "
                "tenant subdomains can't be provisioned."
            )
        return self._api_token

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=CF_API, timeout=20.0)
        return self._client

    def _request(self, method: str, path: str, *, allow_404: bool = False, **kwargs) -> Optional[Dict]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            resp = self._http().request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise TenantDomainError(f"Cloudflare API {method} {path} failed: {exc}") from exc
        if resp.status_code == 404 and allow_404:
            return None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or not body.get("success", False):
            errors = body.get("errors") or resp.text[:300]
            raise TenantDomainError(f"Cloudflare API {method} {path} -> {resp.status_code}: {errors}")
        return body

    # --- Pages custom domain -------------------------------------------------

    def _pages_domains_path(self) -> str:
        return f"/accounts/{self.account_id}/pages/projects/{self.pages_project}/domains"

    def _get_pages_domain(self, hostname: str) -> Optional[Dict]:
        body = self._request("GET", f"{self._pages_domains_path()}/{hostname}", allow_404=True)
        return body.get("result") if body else None

    # --- DNS -------------------------------------------------------------------

    def _dns_records(self, hostname: str) -> List[Dict]:
        body = self._request(
            "GET", f"/zones/{self.zone_id}/dns_records", params={"name": hostname}
        )
        return body.get("result") or []

    def _is_ours(self, record: Dict) -> bool:
        return (
            record.get("type") == "CNAME"
            and (record.get("content") or "").rstrip(".").lower() == self.pages_target
        )

    # --- Public API ------------------------------------------------------------

    @staticmethod
    def hostname_for(tenant_id: str) -> str:
        return f"{tenant_id}.{BASE_DOMAIN}"

    def check_available(self, hostname: str) -> None:
        """Raise TenantDomainConflictError if a foreign DNS record owns ``hostname``."""
        foreign = [r for r in self._dns_records(hostname) if not self._is_ours(r)]
        if foreign:
            kinds = ", ".join(f"{r.get('type')} -> {r.get('content')}" for r in foreign)
            raise TenantDomainConflictError(
                f"{hostname} already has DNS records not managed by the tenant portal ({kinds})."
            )

    def provision(self, hostname: str) -> DomainStatus:
        """Idempotently attach ``hostname`` to the tenant Pages project + create its CNAME."""
        self.check_available(hostname)

        pages = self._get_pages_domain(hostname)
        if pages is None:
            body = self._request("POST", self._pages_domains_path(), json={"name": hostname})
            pages = body.get("result") or {}
            logger.info(f"Added Pages custom domain {hostname} to {self.pages_project}")

        if not any(self._is_ours(r) for r in self._dns_records(hostname)):
            label = hostname[: -len(BASE_DOMAIN) - 1]
            self._request(
                "POST",
                f"/zones/{self.zone_id}/dns_records",
                json={
                    "type": "CNAME",
                    "name": label,
                    "content": self.pages_target,
                    "proxied": True,
                    "comment": "karaoke-gen tenant portal (managed by admin tenant console)",
                },
            )
            logger.info(f"Created DNS CNAME {hostname} -> {self.pages_target}")

        return DomainStatus(hostname=hostname, dns_ok=True, pages_status=pages.get("status"))

    def status(self, hostname: str) -> DomainStatus:
        pages = self._get_pages_domain(hostname)
        dns_ok = any(self._is_ours(r) for r in self._dns_records(hostname))
        return DomainStatus(
            hostname=hostname, dns_ok=dns_ok, pages_status=(pages or {}).get("status")
        )

    def deprovision(self, hostname: str) -> None:
        """Remove the Pages custom domain and OUR CNAME (foreign records are left alone)."""
        if self._get_pages_domain(hostname) is not None:
            self._request("DELETE", f"{self._pages_domains_path()}/{hostname}", allow_404=True)
            logger.info(f"Removed Pages custom domain {hostname}")
        for record in self._dns_records(hostname):
            if self._is_ours(record):
                self._request(
                    "DELETE", f"/zones/{self.zone_id}/dns_records/{record['id']}", allow_404=True
                )
                logger.info(f"Deleted DNS CNAME {hostname}")


_service: Optional[TenantDomainService] = None


def get_tenant_domain_service() -> TenantDomainService:
    global _service
    if _service is None:
        _service = TenantDomainService()
    return _service
