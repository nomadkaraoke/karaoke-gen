"""Tests for git_repos_export module."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import git_repos_export
from git_repos_export import export_git_repos, get_github_token


def _make_repo(owner, name, **over):
    repo = {
        "owner": {"login": owner},
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": True,
        "fork": False,
        "archived": False,
        "default_branch": "main",
        "description": f"{name} desc",
        "size": 100,
        "html_url": f"https://github.com/{owner}/{name}",
        "pushed_at": "2026-08-22T00:00:00Z",
    }
    repo.update(over)
    return repo


def _fake_requests_get(pages):
    """Return a requests.get replacement that serves `pages` by ?page=N (1-based)."""
    def _get(url, headers=None, params=None, timeout=None):
        page = (params or {}).get("page", 1)
        batch = pages[page - 1] if page - 1 < len(pages) else []
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = batch
        return resp
    return _get


def _fake_gcs():
    """Return (fake_client, uploads dict) capturing every blob write."""
    uploads = {}

    def make_blob(path):
        blob = MagicMock()
        blob.upload_from_filename.side_effect = lambda fn: uploads.__setitem__(path, {"kind": "file", "src": fn})
        blob.upload_from_string.side_effect = lambda data, content_type=None: uploads.__setitem__(
            path, {"kind": "string", "data": data, "content_type": content_type}
        )
        return blob

    bucket = MagicMock()
    bucket.blob.side_effect = make_blob
    client = MagicMock()
    client.bucket.return_value = bucket
    return client, uploads


def _run(pages, subprocess_side_effect=None, max_repo_size_kb=git_repos_export.DEFAULT_MAX_REPO_SIZE_KB):
    client, uploads = _fake_gcs()
    run_mock = MagicMock()
    if subprocess_side_effect is not None:
        run_mock.side_effect = subprocess_side_effect
    with patch("git_repos_export.requests.get", side_effect=_fake_requests_get(pages)), \
         patch("git_repos_export.gcs_storage.Client", return_value=client), \
         patch("git_repos_export.shutil.which", return_value="/usr/bin/git"), \
         patch("git_repos_export.subprocess.run", run_mock), \
         patch("git_repos_export.os.path.getsize", return_value=4242), \
         patch("git_repos_export.tempfile.mkdtemp", return_value="/tmp/gitbak-x"), \
         patch("git_repos_export.shutil.rmtree"):
        summary = export_git_repos(
            staging_bucket="staging",
            github_token="ghp_test",
            owners=["nomadkaraoke", "beveradb"],
            max_repo_size_kb=max_repo_size_kb,
        )
    return summary, uploads, run_mock


def _manifest(uploads):
    return json.loads(uploads["git-repos/manifest.json"]["data"])


def test_bundles_every_repo_and_writes_manifest():
    pages = [[
        _make_repo("nomadkaraoke", "karaoke-gen"),
        _make_repo("beveradb", "dotfiles"),
    ]]
    summary, uploads, _ = _run(pages)

    assert "git-repos/nomadkaraoke/karaoke-gen.bundle" in uploads
    assert "git-repos/beveradb/dotfiles.bundle" in uploads
    assert uploads["git-repos/nomadkaraoke/karaoke-gen.bundle"]["kind"] == "file"

    manifest = _manifest(uploads)
    assert manifest["bundled"] == 2
    assert manifest["errors"] == 0
    assert manifest["total"] == 2
    assert {"nomadkaraoke", "beveradb"} == set(manifest["owners"])
    assert all(r["status"] == "bundled" for r in manifest["repos"])
    assert "Bundled 2/2 repos" in summary


def test_forks_and_foreign_owners_excluded():
    pages = [[
        _make_repo("nomadkaraoke", "karaoke-gen"),
        _make_repo("beveradb", "someones-fork", fork=True),
        _make_repo("someoneelse", "unrelated"),
    ]]
    _, uploads, _ = _run(pages)
    manifest = _manifest(uploads)
    assert manifest["total"] == 1
    assert manifest["repos"][0]["full_name"] == "nomadkaraoke/karaoke-gen"


def test_oversized_repo_skipped_without_cloning():
    pages = [[_make_repo("beveradb", "huge", size=20_000_000)]]
    summary, uploads, run_mock = _run(pages, max_repo_size_kb=10_000_000)
    manifest = _manifest(uploads)
    assert manifest["skipped"] == 1
    assert manifest["bundled"] == 0
    assert manifest["repos"][0]["status"] == "skipped_too_large"
    # No git subprocess should have run for a skipped repo.
    run_mock.assert_not_called()
    assert "git-repos/beveradb/huge.bundle" not in uploads


def test_empty_repo_is_a_clean_skip_not_an_error():
    def side_effect(cmd, **kw):
        if "bundle" in cmd:
            raise subprocess.CalledProcessError(
                128, cmd, output=b"", stderr=b"fatal: Refusing to create empty bundle."
            )
        return MagicMock()

    pages = [[_make_repo("beveradb", "empty-repo")]]
    _, uploads, _ = _run(pages, subprocess_side_effect=side_effect)
    manifest = _manifest(uploads)
    assert manifest["skipped"] == 1
    assert manifest["errors"] == 0
    assert manifest["repos"][0]["status"] == "skipped_empty"


def test_single_repo_error_is_recorded_but_others_still_bundle():
    def side_effect(cmd, **kw):
        # Fail the clone of "broken" only.
        if cmd[:2] == ["git", "clone"] and "broken" in cmd[-1]:
            raise subprocess.CalledProcessError(128, cmd, output=b"", stderr=b"fatal: repository not found")
        return MagicMock()

    pages = [[
        _make_repo("nomadkaraoke", "good"),
        _make_repo("nomadkaraoke", "broken"),
    ]]
    summary, uploads, _ = _run(pages, subprocess_side_effect=side_effect)
    manifest = _manifest(uploads)
    assert manifest["bundled"] == 1
    assert manifest["errors"] == 1
    statuses = {r["full_name"]: r["status"] for r in manifest["repos"]}
    assert statuses["nomadkaraoke/good"] == "bundled"
    assert statuses["nomadkaraoke/broken"] == "error"
    assert "git-repos/nomadkaraoke/good.bundle" in uploads


def test_total_failure_raises_but_manifest_still_written():
    def side_effect(cmd, **kw):
        raise subprocess.CalledProcessError(128, cmd, output=b"", stderr=b"fatal: boom")

    pages = [[_make_repo("nomadkaraoke", "only")]]
    client, uploads = _fake_gcs()
    with patch("git_repos_export.requests.get", side_effect=_fake_requests_get(pages)), \
         patch("git_repos_export.gcs_storage.Client", return_value=client), \
         patch("git_repos_export.shutil.which", return_value="/usr/bin/git"), \
         patch("git_repos_export.subprocess.run", side_effect=side_effect), \
         patch("git_repos_export.tempfile.mkdtemp", return_value="/tmp/gitbak-x"), \
         patch("git_repos_export.shutil.rmtree"):
        with pytest.raises(RuntimeError, match="Bundled 0/1"):
            export_git_repos(staging_bucket="staging", github_token="ghp_test", owners=["nomadkaraoke"])
    # Manifest is written before the raise so we still record what happened.
    assert "git-repos/manifest.json" in uploads


def test_empty_token_raises():
    with pytest.raises(ValueError, match="GITHUB_BACKUP_TOKEN"):
        export_git_repos(staging_bucket="staging", github_token="")


def test_missing_git_binary_raises():
    with patch("git_repos_export.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="git binary not found"):
            export_git_repos(staging_bucket="staging", github_token="ghp_test")


def test_get_github_token_returns_value():
    resp = MagicMock()
    resp.payload.data = b"ghp_secretvalue\n"
    client = MagicMock()
    client.access_secret_version.return_value = resp
    with patch("git_repos_export.secretmanager.SecretManagerServiceClient", return_value=client):
        assert get_github_token("nomadkaraoke") == "ghp_secretvalue"


def test_get_github_token_missing_returns_empty_string():
    client = MagicMock()
    client.access_secret_version.side_effect = Exception("NOT_FOUND: no versions")
    with patch("git_repos_export.secretmanager.SecretManagerServiceClient", return_value=client):
        assert get_github_token("nomadkaraoke") == ""
