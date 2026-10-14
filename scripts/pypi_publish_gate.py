#!/usr/bin/env python3
"""Decide whether this push to main should publish karaoke-gen to PyPI.

PyPI caps the karaoke-gen project at 10 GB and each wheel is ~62 MiB, so
publishing on every merge refilled the cap every few weeks (see
docs/PYPI-STORAGE-PRUNE.md). PyPI only serves the public CLI (`pip install
karaoke-gen`); production never installs from it. So we publish only when
something CLI users get has changed since the latest version on PyPI:

  * a path in scripts/pypi_publish_paths.txt changed, or
  * pyproject.toml changed in anything other than its `version = ...` line
    (dependencies, entry points, package includes), or
  * a publish was forced ("[publish-pypi]" in the commit message, or the
    workflow_dispatch input).

The diff base is the version currently on PyPI, not the previous commit, so a
skipped or failed publish can never lose a CLI change: the next push still sees
it. Any doubt (PyPI or git unreachable, tag missing) → publish (fail open).

Prints the decision and, under GitHub Actions, writes ``publish`` and ``reason``
to $GITHUB_OUTPUT and a line to $GITHUB_STEP_SUMMARY. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]
PATHS_FILE = REPO / "scripts" / "pypi_publish_paths.txt"
FORCE_MARKER = "[publish-pypi]"
_VERSION_LINE = re.compile(r"^\s*version\s*=")
_SECTION = re.compile(r"^\s*\[+([^\]]+)\]+")
_PACKAGE_SECTIONS = {"tool.poetry", "project"}


@dataclass
class Decision:
    publish: bool
    reason: str


def load_pathspecs(path: Path = PATHS_FILE) -> list[str]:
    specs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            specs.append(line)
    return specs


def strip_version_line(pyproject_text: str) -> str:
    """pyproject.toml without the package's own `version = ...` line (bumped on
    every PR). Only the one in [tool.poetry] / [project] is dropped; dependency
    tables like [tool.poetry.dependencies.pytest] also have `version =` lines,
    and changes to those must still count."""
    kept, section = [], ""
    for line in pyproject_text.splitlines():
        if m := _SECTION.match(line):
            section = m.group(1).strip()
        elif section in _PACKAGE_SECTIONS and _VERSION_LINE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def latest_pypi_version(project: str) -> str:
    url = f"https://pypi.org/pypi/{project}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https URL)
        return json.load(resp)["info"]["version"]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout


def decide(
    *,
    current_version: str,
    force: bool,
    commit_message: str,
    pathspecs: list[str],
    get_pypi_version: Callable[[], str],
    git: Callable[..., str] = _git,
) -> Decision:
    if force:
        return Decision(True, "forced by workflow_dispatch input")
    if FORCE_MARKER in commit_message:
        return Decision(True, f"forced by {FORCE_MARKER} in the commit message")

    try:
        pypi_version = get_pypi_version()
    except Exception as e:  # noqa: BLE001 — any failure → fail open
        return Decision(True, f"could not read the latest PyPI version ({e})")
    if pypi_version == current_version:
        return Decision(False, f"v{current_version} is already on PyPI")

    base = f"v{pypi_version}"
    try:
        git("fetch", "--no-tags", "--depth=1", "origin", f"refs/tags/{base}:refs/tags/{base}")
        changed = [p for p in git("diff", "--name-only", base, "HEAD", "--", *pathspecs).splitlines() if p]
        old_pyproject = git("show", f"{base}:pyproject.toml")
        new_pyproject = git("show", "HEAD:pyproject.toml")
    except Exception as e:  # noqa: BLE001
        return Decision(True, f"could not diff against PyPI's {base} ({e})")

    if strip_version_line(old_pyproject) != strip_version_line(new_pyproject):
        changed.append("pyproject.toml (beyond the version bump)")
    if not changed:
        return Decision(False, f"no CLI-affecting changes since {base} (latest on PyPI)")
    shown = ", ".join(changed[:10]) + (f" (+{len(changed) - 10} more)" if len(changed) > 10 else "")
    return Decision(True, f"CLI-affecting changes since {base}: {shown}")


def _write_github_outputs(d: Decision) -> None:
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"publish={'true' if d.publish else 'false'}\n")
            f.write(f"reason={d.reason}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(f"### PyPI publish: {'yes' if d.publish else 'skipped'}\n{d.reason}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--project", default="karaoke-gen")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--commit-message", default="")
    args = parser.parse_args(argv)

    d = decide(
        current_version=args.current_version,
        force=args.force,
        commit_message=args.commit_message,
        pathspecs=load_pathspecs(),
        get_pypi_version=lambda: latest_pypi_version(args.project),
    )
    print(f"PyPI publish: {d.publish} ({d.reason})")
    _write_github_outputs(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
