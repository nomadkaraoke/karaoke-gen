"""
Unit tests for TenantDomainService (Cloudflare Pages custom domain + DNS CNAME
for tenant portal subdomains). Cloudflare is faked with an in-memory
httpx.MockTransport so every request/response is exercised without network.
"""
import json

import httpx
import pytest

from backend.services.tenant_domain_service import (
    TenantDomainConflictError,
    TenantDomainError,
    TenantDomainService,
)

ACCOUNT = "acct"
ZONE = "zone"
PROJECT = "karaoke-gen-tenant"
TARGET = f"{PROJECT}.pages.dev"
HOST = "randy-vild.nomadkaraoke.com"


class FakeCloudflare:
    """Minimal stateful model of the Pages domains + DNS records endpoints."""

    def __init__(self):
        self.pages: dict[str, dict] = {}
        self.dns: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_all = False
        self._next_id = 1

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/client/v4")
        self.calls.append((request.method, path))
        assert request.headers["Authorization"] == "Bearer tok"
        if self.fail_all:
            return httpx.Response(500, json={"success": False, "errors": [{"message": "boom"}]})

        pages_prefix = f"/accounts/{ACCOUNT}/pages/projects/{PROJECT}/domains"
        dns_prefix = f"/zones/{ZONE}/dns_records"

        if path == pages_prefix and request.method == "POST":
            name = json.loads(request.content)["name"]
            self.pages[name] = {"name": name, "status": "initializing"}
            return httpx.Response(200, json={"success": True, "result": self.pages[name]})
        if path.startswith(pages_prefix + "/"):
            name = path.rsplit("/", 1)[-1]
            if name not in self.pages:
                return httpx.Response(404, json={"success": False, "errors": [{"code": 8000007}]})
            if request.method == "DELETE":
                del self.pages[name]
                return httpx.Response(200, json={"success": True, "result": None})
            return httpx.Response(200, json={"success": True, "result": self.pages[name]})

        if path == dns_prefix and request.method == "GET":
            name = request.url.params.get("name")
            return httpx.Response(
                200, json={"success": True, "result": [r for r in self.dns if r["name"] == name]}
            )
        if path == dns_prefix and request.method == "POST":
            body = json.loads(request.content)
            record = {
                "id": f"r{self._next_id}",
                "type": body["type"],
                "name": f"{body['name']}.nomadkaraoke.com",
                "content": body["content"],
                "proxied": body["proxied"],
            }
            self._next_id += 1
            self.dns.append(record)
            return httpx.Response(200, json={"success": True, "result": record})
        if path.startswith(dns_prefix + "/") and request.method == "DELETE":
            rid = path.rsplit("/", 1)[-1]
            self.dns = [r for r in self.dns if r["id"] != rid]
            return httpx.Response(200, json={"success": True, "result": {"id": rid}})

        return httpx.Response(404, json={"success": False, "errors": [{"message": f"unhandled {path}"}]})


@pytest.fixture
def cf():
    return FakeCloudflare()


@pytest.fixture
def svc(cf):
    client = httpx.Client(
        base_url="https://api.cloudflare.com/client/v4", transport=httpx.MockTransport(cf.handler)
    )
    return TenantDomainService(
        api_token="tok", client=client, account_id=ACCOUNT, zone_id=ZONE, pages_project=PROJECT
    )


def test_provision_creates_pages_domain_and_proxied_cname(svc, cf):
    status = svc.provision(HOST)
    assert HOST in cf.pages
    assert cf.dns == [
        {"id": "r1", "type": "CNAME", "name": HOST, "content": TARGET, "proxied": True}
    ]
    assert status.dns_ok is True
    assert status.pages_status == "initializing"
    assert status.to_dict()["state"] == "provisioning"


def test_provision_is_idempotent(svc, cf):
    svc.provision(HOST)
    svc.provision(HOST)
    assert len(cf.dns) == 1
    assert [c for c in cf.calls if c[0] == "POST"] == [
        ("POST", f"/accounts/{ACCOUNT}/pages/projects/{PROJECT}/domains"),
        ("POST", f"/zones/{ZONE}/dns_records"),
    ]


def test_status_active_once_cert_issued(svc, cf):
    svc.provision(HOST)
    cf.pages[HOST]["status"] = "active"
    assert svc.status(HOST).to_dict()["state"] == "active"


def test_status_missing(svc):
    assert svc.status(HOST).to_dict() == {
        "hostname": HOST, "state": "missing", "dns_ok": False, "pages_status": None,
    }


def test_foreign_dns_record_blocks_provisioning(svc, cf):
    cf.dns.append({"id": "x", "type": "A", "name": "decide.nomadkaraoke.com", "content": "1.2.3.4"})
    with pytest.raises(TenantDomainConflictError, match="decide.nomadkaraoke.com"):
        svc.provision("decide.nomadkaraoke.com")
    assert cf.pages == {}  # nothing created


def test_foreign_cname_blocks_provisioning(svc, cf):
    cf.dns.append({"id": "x", "type": "CNAME", "name": HOST, "content": "elsewhere.example.com"})
    with pytest.raises(TenantDomainConflictError):
        svc.check_available(HOST)


def test_deprovision_removes_only_our_records(svc, cf):
    svc.provision(HOST)
    cf.dns.append({"id": "keep", "type": "TXT", "name": HOST, "content": "verification"})
    svc.deprovision(HOST)
    assert HOST not in cf.pages
    assert [r["id"] for r in cf.dns] == ["keep"]


def test_deprovision_when_nothing_exists_is_noop(svc, cf):
    svc.deprovision(HOST)
    assert not [c for c in cf.calls if c[0] == "DELETE"]


def test_api_errors_raise_tenant_domain_error(svc, cf):
    cf.fail_all = True
    with pytest.raises(TenantDomainError, match="500"):
        svc.provision(HOST)


def test_missing_token_raises(monkeypatch):
    from backend.services import tenant_domain_service as tds

    class NoSecrets:
        def get_secret(self, _):
            return None

    monkeypatch.setattr(tds, "get_settings", lambda: NoSecrets())
    with pytest.raises(TenantDomainError, match="not configured"):
        TenantDomainService().status(HOST)
