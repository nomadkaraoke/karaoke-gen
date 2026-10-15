"""Unit tests for scripts/build_import_list.py: the ownership exclusions must hold, or a re-import
would make this stack fight gen's edge stack, gen's runtime tenant domains or decide's stack."""
import importlib.util
import io
import json
import pathlib
from contextlib import redirect_stdout

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_import_list.py"
ACCOUNT, ZONE = "acct", "zone1"


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    spec = importlib.util.spec_from_file_location("build_import_list", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def _run(api):
        monkeypatch.setattr(mod, "cf", lambda path: api.get(path.split("?")[0], []))
        buf = io.StringIO()
        with redirect_stdout(buf):
            mod.main()
        return {r["name"]: r for r in json.loads(buf.getvalue())["resources"]}

    return _run


def fake_api(**over):
    api = {
        f"/zones": [{"name": "nomadkaraoke.com", "id": ZONE}],
        f"/accounts/{ACCOUNT}/pages/projects": [
            {"name": "karaoke-gen", "subdomain": "karaoke-gen-9k4.pages.dev",
             "domains": ["karaoke-gen-9k4.pages.dev", "gen.nomadkaraoke.com"]},
            {"name": "karaoke-gen-tenant", "subdomain": "karaoke-gen-tenant-x0h.pages.dev",
             "domains": ["singa.nomadkaraoke.com"]},
        ],
        f"/zones/{ZONE}/dns_records": [
            {"id": "r1", "type": "CNAME", "name": "gen.nomadkaraoke.com", "content": "karaoke-gen-9k4.pages.dev"},
            {"id": "r2", "type": "CNAME", "name": "api.nomadkaraoke.com", "content": "ghs.googlehosted.com"},
            {"id": "r3", "type": "CNAME", "name": "singa.nomadkaraoke.com", "content": "karaoke-gen-tenant-x0h.pages.dev"},
            {"id": "r4", "type": "MX", "name": "nomadkaraoke.com", "content": "route1.mx.cloudflare.net",
             "meta": {"read_only": True, "email_routing": True}},
            {"id": "r5", "type": "TXT", "name": "nomadkaraoke.com", "content": "v=spf1"},
            {"id": "r6", "type": "TXT", "name": "nomadkaraoke.com", "content": "google-site-verification=x"},
        ],
        f"/zones/{ZONE}/rulesets": [
            {"id": "rs1", "kind": "zone", "phase": "http_request_dynamic_redirect"},
            {"id": "rs2", "kind": "zone", "phase": "http_ratelimit"},
            {"id": "rs3", "kind": "managed", "phase": "ddos_l7"},
        ],
        f"/zones/{ZONE}/email/routing": {"enabled": False},
        f"/zones/{ZONE}/workers/routes": [
            {"id": "w1", "pattern": "decide.nomadkaraoke.com/api/*"},
            {"id": "w2", "pattern": "create.karaokehunt.com/*"},
        ],
        f"/accounts/{ACCOUNT}/access/identity_providers": [
            {"id": "i1", "type": "cloudflare"}, {"id": "i2", "type": "onetimepin"}],
    }
    api.update(over)
    return api


def test_excludes_resources_owned_elsewhere(run):
    names = run(fake_api())
    dns_ids = {r["id"] for r in names.values() if "dnsRecord" in r["type"]}
    assert f"{ZONE}/r1" in dns_ids  # gen consumer Pages CNAME is ours
    assert f"{ZONE}/r2" not in dns_ids  # api: gen edge stack
    assert f"{ZONE}/r3" not in dns_ids  # tenant CNAME: gen backend at runtime
    assert f"{ZONE}/r4" not in dns_ids  # read-only, owned by Email Routing
    rulesets = [r["id"] for r in names.values() if "ruleset" in r["type"]]
    assert rulesets == [f"zones/{ZONE}/rs1"]  # gen owns ratelimit; managed rulesets skipped
    routes = [r["id"] for r in names.values() if "workersRoute" in r["type"]]
    assert routes == [f"{ZONE}/w2"]  # decide's route stays in decide's stack
    idps = [r["id"] for r in names.values() if "IdentityProvider" in r["type"]]
    assert idps == [f"accounts/{ACCOUNT}/i2"]  # built-in "cloudflare" IdP skipped


def test_pages_domains_skip_tenant_project_and_pages_dev(run):
    names = run(fake_api())
    domains = [r["id"] for r in names.values() if "pagesDomain" in r["type"]]
    assert domains == [f"{ACCOUNT}/karaoke-gen/gen.nomadkaraoke.com"]


def test_duplicate_logical_names_get_suffixes(run):
    names = run(fake_api())
    txt = sorted(n for n in names if n.startswith("dns-nomadkaraoke-com-apex-txt"))
    assert txt == ["dns-nomadkaraoke-com-apex-txt", "dns-nomadkaraoke-com-apex-txt-2"]
    assert len(names) == len({r["name"] for r in names.values()})
