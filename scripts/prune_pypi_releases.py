#!/usr/bin/env python3
"""Compute (and help you execute) a PyPI storage-retention prune for karaoke-gen.

PyPI caps total *project* size at 10 GB. karaoke-gen auto-publishes a ~62 MiB
universal wheel on every merge to main (~3 GB/month), so the project refills the
cap every few months and `poetry publish` then fails with HTTP 400 "Project size
too large". See docs/PYPI-STORAGE-PRUNE.md for the full story.

PyPI offers **no deletion API** (upload tokens and Trusted Publishing are
upload-only), so this script never deletes anything itself. It:

  1. reads the project's release list from the public JSON API,
  2. computes which releases to KEEP vs DELETE under the retention policy —
     "keep every release younger than --keep-days, plus the most recent release
     of each older calendar month, plus the latest release overall" — and
  3. emits the plan as a table / JSON / Markdown report, and a ready-to-paste
     **browser-console snippet** that performs the deletions from your logged-in
     PyPI session (the only mechanism PyPI supports).

A scheduled GitHub Action (.github/workflows/pypi-prune-reminder.yml) runs this
monthly and posts the plan to a tracking issue; you paste the snippet into the
console on your logged-in PyPI releases page to actually prune.

Deletion is irreversible and a deleted version/filename can never be re-uploaded.
For karaoke-gen this is safe: nothing builds from source and the GCE encoding
worker pulls the wheel from GCS, not PyPI — PyPI is purely the public `pip
install` channel, whose users want recent versions.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.request
from dataclasses import dataclass, field

PYPI_TOTAL_SIZE_CAP_GB = 10.0  # PyPI's default project-wide storage limit.


@dataclass
class ReleasePlan:
    """Result of applying the retention policy to a project's releases."""

    keep: list[str] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)  # version -> bytes
    current_bytes: int = 0
    delete_bytes: int = 0
    keep_days: int = 0

    @property
    def kept_bytes(self) -> int:
        return self.current_bytes - self.delete_bytes


def _version_timestamp(files: list[dict]) -> str | None:
    """Latest upload timestamp (full ISO 8601) across a version's files.

    Returns the raw ``upload_time_iso_8601`` string (e.g.
    ``2026-08-29T23:56:57.396484Z``), or None if no file is dated. PyPI always
    reports these in UTC with a fixed format, so lexicographic ordering equals
    chronological ordering — we keep the full timestamp (not just the day) so
    same-day releases are ranked by actual upload time, independent of the JSON
    API's mapping order.
    """
    stamps = [f["upload_time_iso_8601"] for f in files if f.get("upload_time_iso_8601")]
    if not stamps:
        return None
    return max(stamps)


def compute_plan(
    releases: dict[str, list[dict]],
    *,
    keep_days: int,
    now: datetime.date,
) -> ReleasePlan:
    """Decide which versions to keep vs delete.

    ``releases`` is the ``releases`` object from PyPI's JSON API: a mapping of
    version string -> list of uploaded-file dicts (each with ``size`` and
    ``upload_time_iso_8601``).

    Policy: keep every version whose latest upload is within ``keep_days`` of
    ``now``; for anything older, keep only the newest version of each calendar
    month; always keep the newest version overall. Empty releases (all files
    yanked/removed) contribute nothing and are ignored.
    """
    plan = ReleasePlan(keep_days=keep_days)

    # Collect (version, full_timestamp, size) for every non-empty release. The
    # full ISO timestamp (not just the day) drives every chronological
    # comparison so same-day releases rank by real upload time.
    dated: list[tuple[str, str, int]] = []
    for version, files in releases.items():
        if not files:
            continue
        ts = _version_timestamp(files)
        if ts is None:
            continue
        size = sum(int(f.get("size") or 0) for f in files)
        dated.append((version, ts, size))
        plan.sizes[version] = size
        plan.current_bytes += size

    if not dated:
        return plan

    def _age_days(ts: str) -> int:
        return (now - datetime.date.fromisoformat(ts[:10])).days

    # Always keep the newest release overall, regardless of policy (full-
    # timestamp max, so a same-day burst keeps the genuinely-latest one).
    newest_version = max(dated, key=lambda d: d[1])[0]

    # For releases older than the window, keep the newest per calendar month.
    month_latest: dict[str, tuple[str, str]] = {}  # "YYYY-MM" -> (timestamp, version)
    for version, ts, _ in dated:
        if _age_days(ts) <= keep_days:
            continue  # inside the full-fidelity window; handled below
        ym = ts[:7]
        if ym not in month_latest or ts > month_latest[ym][0]:
            month_latest[ym] = (ts, version)
    monthly_keepers = {v for _, v in month_latest.values()}

    for version, ts, size in dated:
        keep = _age_days(ts) <= keep_days or version == newest_version or version in monthly_keepers
        if keep:
            plan.keep.append(version)
        else:
            plan.delete.append(version)
            plan.delete_bytes += size

    # Present most-recent-first so a human scanning the delete list sees the
    # boundary of what's being removed.
    ts_by_version = {v: ts for v, ts, _ in dated}
    plan.keep.sort(key=lambda v: ts_by_version[v], reverse=True)
    plan.delete.sort(key=lambda v: ts_by_version[v], reverse=True)
    return plan


def fetch_releases(project: str) -> dict[str, list[dict]]:
    """Fetch the ``releases`` map from PyPI's public JSON API (stdlib only)."""
    url = f"https://pypi.org/pypi/{project}/json"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
        data = json.load(resp)
    return data.get("releases", {})


def _gib(n_bytes: int) -> float:
    return n_bytes / (1024**3)


def _gb(n_bytes: int) -> float:
    return n_bytes / 1e9


def render_console_js(project: str, versions: list[str]) -> str:
    """A snippet to paste into the browser console on the logged-in PyPI page:
    https://pypi.org/manage/project/<project>/releases/

    It reads the session CSRF token from the page and POSTs a whole-version
    delete for each listed version, ~500ms apart, logging progress.
    """
    versions_json = json.dumps(versions)
    return f"""// Paste on https://pypi.org/manage/project/{project}/releases/ while logged in.
(async () => {{
  const project = {json.dumps(project)};
  const versions = {versions_json};
  const el = document.querySelector('input[name="csrf_token"]');
  if (!el) {{ console.error("No csrf_token on page — are you on the releases management page, logged in?"); return; }}
  const csrf = el.value;
  let ok = 0, fail = 0;
  for (const v of versions) {{
    try {{
      const res = await fetch(`/manage/project/${{project}}/release/${{encodeURIComponent(v)}}/`, {{
        method: "POST",
        credentials: "include",
        headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
        body: `csrf_token=${{encodeURIComponent(csrf)}}&confirm_delete_version=${{encodeURIComponent(v)}}`,
      }});
      if (res.ok || res.redirected) {{ ok++; console.log(`✓ deleted ${{v}} (${{ok}}/${{versions.length}})`); }}
      else {{ fail++; console.warn(`✗ ${{v}} → HTTP ${{res.status}}`); }}
    }} catch (e) {{ fail++; console.warn(`✗ ${{v}}`, e); }}
    await new Promise(r => setTimeout(r, 500));
  }}
  console.log(`Done. deleted=${{ok}} failed=${{fail}}. Reload to confirm.`);
}})();
"""


def render_table(plan: ReleasePlan) -> str:
    lines = [
        f"Project size: {_gb(plan.current_bytes):.2f} GB " f"({_gib(plan.current_bytes):.2f} GiB) of {PYPI_TOTAL_SIZE_CAP_GB:.0f} GB cap",
        f"Policy: keep last {plan.keep_days} days + newest release per older month + latest overall",
        f"Keep:   {len(plan.keep)} releases",
        f"Delete: {len(plan.delete)} releases  " f"(reclaims {_gb(plan.delete_bytes):.2f} GB → {_gb(plan.kept_bytes):.2f} GB after)",
    ]
    if plan.delete:
        lines.append("")
        lines.append("Would delete:")
        lines.extend(f"  - {v}" for v in plan.delete)
    return "\n".join(lines) + "\n"


def render_json(project: str, plan: ReleasePlan) -> str:
    return json.dumps(
        {
            "project": project,
            "keep_days": plan.keep_days,
            "current_gb": _gb(plan.current_bytes),
            "kept_gb": _gb(plan.kept_bytes),
            "reclaimable_gb": _gb(plan.delete_bytes),
            "cap_gb": PYPI_TOTAL_SIZE_CAP_GB,
            "keep_count": len(plan.keep),
            "delete_count": len(plan.delete),
            "delete_versions": plan.delete,
        },
        indent=2,
    )


def render_markdown(project: str, plan: ReleasePlan, now: datetime.date) -> str:
    cur = _gb(plan.current_bytes)
    after = _gb(plan.kept_bytes)
    reclaim = _gb(plan.delete_bytes)
    urgent = cur > 8.5
    header = "## 🧹 PyPI storage prune due" + (" — ⚠️ NEAR CAP" if urgent else "")
    manage_url = f"https://pypi.org/manage/project/{project}/releases/"
    md = [
        header,
        "",
        f"`{project}` is at **{cur:.2f} GB** of PyPI's **{PYPI_TOTAL_SIZE_CAP_GB:.0f} GB** "
        f"project cap ({now.isoformat()}). Prune to keep publishing.",
        "",
        "| | Count | Size |",
        "|---|---:|---:|",
        f"| **Keep** | {len(plan.keep)} | {after:.2f} GB |",
        f"| **Delete** | {len(plan.delete)} | −{reclaim:.2f} GB |",
        f"| **After prune** | {len(plan.keep)} | **{after:.2f} GB** |",
        "",
        f"**Policy:** keep every release from the last **{plan.keep_days} days**, "
        "plus the newest release of each older calendar month, plus the latest overall.",
        "",
        "### How to prune (≈1 minute, no secrets)",
        "",
        f"1. Open **<{manage_url}>** (log in if needed).",
        "2. Open your browser devtools **Console**.",
        "3. Paste the snippet below and press Enter. It deletes exactly the "
        "planned versions using your session, ~0.5s apart, logging progress.",
        "4. Reload the page to confirm; re-run `poetry publish` / the CI publish " "step if it was blocked.",
        "",
        "```js",
        render_console_js(project, plan.delete).rstrip(),
        "```",
        "",
        "<details><summary>Full delete list " f"({len(plan.delete)} releases)</summary>",
        "",
        *(f"- `{v}`" for v in plan.delete),
        "",
        "</details>",
        "",
        "---",
        "_Deletion is irreversible and versions can't be re-uploaded. Safe here: "
        "nothing builds karaoke-gen from source and the encoding worker pulls the "
        "wheel from GCS — PyPI is only the public `pip install` channel. "
        "Regenerate this plan any time with `python scripts/prune_pypi_releases.py`._",
    ]
    return "\n".join(md) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="karaoke-gen")
    parser.add_argument(
        "--keep-days",
        type=int,
        default=60,
        help="Keep every release younger than this many days (default: 60).",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "markdown", "console-js"],
        default="table",
    )
    parser.add_argument(
        "--output",
        help="Write to this file instead of stdout.",
    )
    parser.add_argument(
        "--now",
        help="Override 'today' as YYYY-MM-DD (for testing/reproducibility).",
    )
    args = parser.parse_args(argv)

    now = datetime.date.fromisoformat(args.now) if args.now else datetime.datetime.now(datetime.timezone.utc).date()

    releases = fetch_releases(args.project)
    plan = compute_plan(releases, keep_days=args.keep_days, now=now)

    if args.format == "table":
        out = render_table(plan)
    elif args.format == "json":
        out = render_json(args.project, plan) + "\n"
    elif args.format == "markdown":
        out = render_markdown(args.project, plan, now)
    else:  # console-js
        out = render_console_js(args.project, plan.delete)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
