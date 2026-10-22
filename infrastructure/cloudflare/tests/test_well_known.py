"""kjbox.cc/.well-known/*: the Worker serves the app-link files and the redirect rule excludes the path."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import well_known

ROOT = pathlib.Path(__file__).resolve().parents[1]
FP = "AA:BB:CC:DD"


def test_redirect_rule_excludes_well_known_and_is_otherwise_unchanged():
    src = (ROOT / "rulesets.py").read_text()
    assert "WELL_KNOWN_EXCLUSION" in src
    assert 'starts_with(http.request.uri.path, "/.well-known/")' in well_known.WELL_KNOWN_EXCLUSION
    # The code-entry rule (path eq "/") is untouched.
    assert '"(http.host in {\\"kjbox.cc\\" \\"www.kjbox.cc\\"} and http.request.uri.path eq \\"/\\")"' in src
    assert 'http.request.uri.path ne \\"/\\"" + WELL_KNOWN_EXCLUSION + ")"' in src


def test_build_worker_script_bakes_fingerprints():
    assert "ANDROID_SHA256_FINGERPRINTS = []" in well_known.build_worker_script([])
    assert f'ANDROID_SHA256_FINGERPRINTS = ["{FP}"]' in well_known.build_worker_script([FP])


def _run_worker(fps, path, method="GET"):
    script = well_known.build_worker_script(fps)
    js = script.replace("export default", "const worker =") + f"""
worker.fetch(new Request("https://kjbox.cc{path}", {{method: "{method}"}})).then(async r =>
  console.log(JSON.stringify({{status: r.status, ct: r.headers.get("content-type"), body: await r.text()}})));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestWorker:
    def test_assetlinks_with_fingerprint(self):
        r = _run_worker([FP], "/.well-known/assetlinks.json")
        assert r["status"] == 200 and r["ct"] == "application/json"
        assert json.loads(r["body"]) == [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": "com.nomadkaraoke.mobile",
                       "sha256_cert_fingerprints": [FP]}}]

    def test_assetlinks_empty_when_no_fingerprints(self):
        assert json.loads(_run_worker([], "/.well-known/assetlinks.json")["body"]) == []

    def test_aasa(self):
        r = _run_worker([], "/.well-known/apple-app-site-association")
        assert r["status"] == 200 and r["ct"] == "application/json"
        assert json.loads(r["body"]) == {"applinks": {"details": [{
            "appIDs": ["55B76HN73D.com.nomadkaraoke.mobile"],
            "components": [{"/": "/", "?": {"t": "?*"}}, {"/": "/*"}]}]}}

    def test_unknown_path_404_and_post_405(self):
        assert _run_worker([], "/.well-known/other")["status"] == 404
        assert _run_worker([], "/.well-known/assetlinks.json", "POST")["status"] == 405


def test_routes_cover_apex_and_www():
    # The redirect exclusion matches both hosts, so both need the Worker route.
    assert sorted(well_known.ROUTE_PATTERNS.values()) == ["kjbox.cc/.well-known/*", "www.kjbox.cc/.well-known/*"]
    src = (ROOT / "pages_workers.py").read_text()
    assert "for _name, _pattern in ROUTE_PATTERNS.items()" in src
