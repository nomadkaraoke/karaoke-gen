"""Git repository backup to GCS staging bucket.

Mirrors every repo under the configured GitHub owners (the ``nomadkaraoke`` org
and the ``beveradb`` personal account) into a single ``git bundle`` per repo, so
the full commit history survives even a complete loss of GitHub access — e.g. an
account ban from an automated copyright false-positive. Each bundle is a
self-contained file you can ``git clone`` directly, with no GitHub involved.

Why bundles: ``git bundle create <file> --all`` packages every branch and tag
(the entire reachable history) into one portable file. Restore is just
``git clone repo.bundle repo`` then push to a new remote. See
docs/DISASTER-RECOVERY.md § "Scenario 3: Loss of GitHub access".

The ``git`` binary is invoked via subprocess; it ships in the Cloud Functions
gen2 (Ubuntu-based) runtime. Repos are processed one at a time and the local
clone is deleted before the next, so peak local (tmpfs) usage is one repo's
working set, not the sum of all repos.

A ``manifest.json`` alongside the bundles records exactly which repos existed at
backup time (visibility, default branch, description, size, fork/archived flags)
— essential when recreating repos and settings from scratch after a ban.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile

import requests
from google.cloud import secretmanager
from google.cloud import storage as gcs_storage

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DEFAULT_OWNERS = ["nomadkaraoke", "beveradb"]

# Skip repos whose GitHub-reported size exceeds this (KB). Guards against a
# runaway huge repo exhausting the function's in-memory /tmp or the 30-min
# deadline. Code repos are single-digit MBs; the default (10 GB) only excludes
# pathological cases, which are logged and recorded in the manifest as skipped.
DEFAULT_MAX_REPO_SIZE_KB = 10_000_000

# Safety cap on pagination so a bug (or a token with access to thousands of
# repos) can never loop unbounded. 30 pages * 100 per_page = 3000 repos.
_MAX_PAGES = 30

# Per-subprocess timeouts (seconds). Clone dominates; bundle is CPU on a local
# mirror. Both are well under the function's 1800s deadline.
_CLONE_TIMEOUT = 1200
_BUNDLE_TIMEOUT = 600


def get_github_token(project: str, secret_id: str = "github-backup-token") -> str:
    """Fetch the GitHub backup PAT from Secret Manager. Returns "" if unset.

    Read at runtime (not a deploy-time secret env var) so the Cloud Function
    still deploys before the secret has any version — the git-repo backup step
    simply skips until a token value is added. Any access error (no version,
    permission, network) is treated as "no token" so it never aborts the wider
    nightly pipeline.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    try:
        resp = client.access_secret_version(request={"name": name})
    except Exception as e:  # noqa: BLE001 — absent/inaccessible token = skip, not fail
        logger.warning(f"Could not read {secret_id} (git-repo backup will skip): {e}")
        return ""
    return resp.payload.data.decode("utf-8").strip()


def _gh_get(path: str, token: str, params: dict | None = None) -> requests.Response:
    resp = requests.get(
        f"{GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def _list_repos(token: str, owners: list[str], include_forks: bool) -> list[dict]:
    """List all repos accessible to the token whose owner is in ``owners``.

    Uses the authenticated-user endpoint (``/user/repos``) so **private** repos
    are included. A single paginated sweep with
    ``affiliation=owner,organization_member`` covers both the personal account
    (owner) and org repos the token holder belongs to (organization_member); we
    then filter down to the owners we actually want to back up.
    """
    owners_lc = {o.lower() for o in owners}
    repos: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        resp = _gh_get(
            "/user/repos",
            token,
            params={
                "affiliation": "owner,organization_member",
                "visibility": "all",
                "per_page": 100,
                "page": page,
            },
        )
        batch = resp.json()
        if not batch:
            break
        for r in batch:
            if r["owner"]["login"].lower() not in owners_lc:
                continue
            if r.get("fork") and not include_forks:
                continue
            repos.append(r)
    else:
        logger.warning(f"Hit pagination cap ({_MAX_PAGES} pages) — repo list may be truncated")

    # Back up owners in the order they were configured (nomadkaraoke before
    # beveradb by default), then by name. If the 30-min deadline is ever hit on
    # a large personal account, the business-critical org repos are already done.
    owner_rank = {o.lower(): i for i, o in enumerate(owners)}
    repos.sort(key=lambda r: (owner_rank.get(r["owner"]["login"].lower(), len(owner_rank)), r["name"].lower()))
    return repos


def _git_auth_env(token: str) -> dict:
    """Git environment that authenticates via an ``Authorization`` header
    injected through ``GIT_CONFIG_*`` rather than the clone URL.

    Keeping the PAT out of the URL (and therefore out of ``argv`` and out of
    git's own error output, which echoes the *clean* URL) means it can't leak
    via ``ps``, ``subprocess.TimeoutExpired``, or captured stderr. This mirrors
    how ``actions/checkout`` injects credentials. ``GIT_TERMINAL_PROMPT=0``
    guarantees git fails fast on a private/renamed/deleted repo instead of
    blocking on a credential prompt.
    """
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _redact(text: str, token: str) -> str:
    """Belt-and-suspenders: strip the PAT from any text before it's persisted."""
    if token and text:
        return text.replace(token, "***")
    return text


def _bundle_repo(repo: dict, token: str, workdir: str) -> str:
    """Mirror-clone ``repo`` and produce a single-file bundle of all refs.

    Returns the local path to the ``.bundle`` file. Raises
    ``subprocess.CalledProcessError`` on git failure (empty repos included — the
    caller distinguishes those from real errors).
    """
    owner = repo["owner"]["login"]
    name = repo["name"]
    mirror_dir = os.path.join(workdir, f"{name}.git")
    bundle_path = os.path.join(workdir, f"{name}.bundle")

    # Clean URL (no credentials) — auth is supplied out-of-band via _git_auth_env.
    clean_url = f"https://github.com/{owner}/{name}.git"
    env = _git_auth_env(token)

    subprocess.run(
        ["git", "clone", "--mirror", clean_url, mirror_dir],
        check=True,
        capture_output=True,
        env=env,
        timeout=_CLONE_TIMEOUT,
    )
    # --all captures every branch and tag (all reachable history). An empty repo
    # has no refs and `git bundle create --all` fails with "Refusing to create
    # empty bundle" — the caller treats that as a clean skip, not an error.
    subprocess.run(
        ["git", "-C", mirror_dir, "bundle", "create", bundle_path, "--all"],
        check=True,
        capture_output=True,
        env=env,
        timeout=_BUNDLE_TIMEOUT,
    )
    return bundle_path


def _repo_entry(repo: dict) -> dict:
    """Extract the manifest metadata worth preserving for a restore."""
    return {
        "full_name": repo.get("full_name"),
        "private": repo.get("private"),
        "fork": repo.get("fork"),
        "archived": repo.get("archived"),
        "default_branch": repo.get("default_branch"),
        "description": repo.get("description"),
        "size_kb": repo.get("size"),
        "html_url": repo.get("html_url"),
        "pushed_at": repo.get("pushed_at"),
    }


def export_git_repos(
    staging_bucket: str,
    github_token: str,
    owners: list[str] | None = None,
    include_forks: bool = False,
    max_repo_size_kb: int = DEFAULT_MAX_REPO_SIZE_KB,
    staging_prefix: str = "git-repos/",
) -> str:
    """Bundle every repo under ``owners`` and write to the GCS staging bucket.

    Bundles land at ``{staging_prefix}{owner}/{repo}.bundle`` and a
    ``{staging_prefix}manifest.json`` records the full repo inventory. The
    nightly S3 upload step then ships them off-site (they are small, so unlike
    Firestore they upload every night, not weekly).

    Per-repo failures are recorded in the manifest and counted in the summary
    but do not abort the run (mirrors ``secrets_export`` behaviour). Only a
    systemic failure — no token, missing git binary, or every repo erroring —
    raises, so a single flaky repo doesn't turn the whole nightly report red.

    Args:
        staging_bucket: GCS staging bucket name.
        github_token: PAT with read access (``repo`` + ``read:org``) to the owners.
        owners: GitHub owners (org/user logins) to back up. Defaults to
            ``["nomadkaraoke", "beveradb"]``.
        include_forks: Back up forked repos too (default False — forks are
            recoverable from upstream).
        max_repo_size_kb: Skip repos larger than this (GitHub-reported size).
        staging_prefix: Prefix under the staging bucket for bundles/manifest.

    Returns:
        Summary string.
    """
    if not github_token:
        raise ValueError("GITHUB_BACKUP_TOKEN is empty — cannot enumerate/clone repos")

    if shutil.which("git") is None:
        raise RuntimeError("git binary not found in runtime — cannot bundle repos")

    owners = owners or DEFAULT_OWNERS

    repos = _list_repos(github_token, owners, include_forks)
    logger.info(f"Found {len(repos)} repos to back up across {owners}")

    gcs_client = gcs_storage.Client()
    bucket = gcs_client.bucket(staging_bucket)

    manifest: dict = {"owners": owners, "repos": []}
    bundled = errors = skipped = 0

    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        full = f"{owner}/{name}"
        entry = _repo_entry(repo)

        size_kb = repo.get("size") or 0
        if size_kb > max_repo_size_kb:
            entry["status"] = "skipped_too_large"
            skipped += 1
            logger.warning(f"Skipping {full}: size {size_kb}KB > {max_repo_size_kb}KB")
            manifest["repos"].append(entry)
            continue

        workdir = tempfile.mkdtemp(prefix="gitbak-")
        try:
            bundle_path = _bundle_repo(repo, github_token, workdir)
            dst = f"{staging_prefix}{owner}/{name}.bundle"
            bucket.blob(dst).upload_from_filename(bundle_path)
            entry["status"] = "bundled"
            entry["bundle_bytes"] = os.path.getsize(bundle_path)
            bundled += 1
        except subprocess.CalledProcessError as e:
            stderr = _redact((e.stderr or b"").decode("utf-8", "replace"), github_token)
            # An empty repo (no commits yet) legitimately has nothing to bundle.
            if "empty bundle" in stderr.lower() or "does not have any commits" in stderr.lower():
                entry["status"] = "skipped_empty"
                skipped += 1
            else:
                entry["status"] = "error"
                entry["error"] = stderr[-500:]
                errors += 1
                logger.error(f"Failed to bundle {full}: {stderr[-500:]}")
        except Exception as e:  # noqa: BLE001 — record and continue, never abort the sweep
            entry["status"] = "error"
            entry["error"] = _redact(str(e), github_token)
            errors += 1
            logger.error(f"Failed to bundle {full}: {entry['error']}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        manifest["repos"].append(entry)

    manifest["bundled"] = bundled
    manifest["errors"] = errors
    manifest["skipped"] = skipped
    manifest["total"] = len(repos)

    # Always write the manifest — even on a bad run it records what we saw.
    bucket.blob(f"{staging_prefix}manifest.json").upload_from_string(
        json.dumps(manifest, indent=2, sort_keys=True),
        content_type="application/json",
    )

    summary = (
        f"Bundled {bundled}/{len(repos)} repos "
        f"({errors} errors, {skipped} skipped) to gs://{staging_bucket}/{staging_prefix}"
    )
    logger.info(summary)

    # Systemic failure: nothing bundled successfully yet repos errored → surface
    # it as a hard error (bad token, git broken, network down). A run where every
    # repo was a *benign* skip (empty/oversized) or where at least one bundled is
    # not systemic, so it stays green with the counts noted in the summary.
    if bundled == 0 and errors > 0:
        raise RuntimeError(summary)

    return summary
