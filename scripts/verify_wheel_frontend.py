#!/usr/bin/env python3
"""Verify a built karaoke-gen wheel actually bundles the Next.js frontend export.

The frontend static export (``karaoke_gen/nextjs_frontend/out``) is generated at
build time and gitignored (issue #876). It is pulled into the wheel via the
``include`` entry in ``pyproject.toml``. If the frontend build is skipped or
produces no output, poetry will still happily build a wheel — just without the
frontend — and the CLI local-review UI would silently break in production.

This script is the loud guard against that: it fails (non-zero exit) unless the
wheel contains a plausible frontend export. It also fails when the wheel is close
to PyPI's 100 MiB per-file limit, so growth shows up on the PR instead of as a
failed publish after merge (2026-10-27..29: 110 MB wheels, PyPI stuck at 0.279.4).

Usage:
    python scripts/verify_wheel_frontend.py dist/karaoke_gen-*.whl
"""

import os
import sys
import zipfile
from glob import glob

FRONTEND_PREFIX = "karaoke_gen/nextjs_frontend/out/"
# A real 33-locale export is thousands of files; require a sane floor plus the
# root entrypoint so an empty/partial copy can't sneak through.
MIN_FRONTEND_FILES = 50
REQUIRED_ENTRY = FRONTEND_PREFIX + "index.html"
# PyPI rejects files over 100 MiB; leave headroom so we fail before publish does.
MAX_WHEEL_BYTES = 95 * 1024 * 1024


def resolve_wheel(patterns: list[str]) -> str:
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(glob(pattern))
    if not matches:
        print(f"::error::No wheel found matching: {' '.join(patterns)}")
        sys.exit(1)
    if len(matches) > 1:
        print(f"::error::Expected exactly one wheel, found {len(matches)}: {matches}")
        sys.exit(1)
    return matches[0]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: verify_wheel_frontend.py <wheel-path-or-glob> [...]")
        sys.exit(2)

    wheel = resolve_wheel(sys.argv[1:])
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()

    frontend_files = [n for n in names if n.startswith(FRONTEND_PREFIX) and not n.endswith("/")]
    count = len(frontend_files)

    if REQUIRED_ENTRY not in names:
        print(f"::error::{wheel} is missing the frontend entrypoint '{REQUIRED_ENTRY}'.")
        print("The frontend was not built into the package before 'poetry build'.")
        sys.exit(1)

    if count < MIN_FRONTEND_FILES:
        print(
            f"::error::{wheel} bundles only {count} frontend files under "
            f"'{FRONTEND_PREFIX}' (expected >= {MIN_FRONTEND_FILES}). "
            "The frontend export looks empty or partial."
        )
        sys.exit(1)

    size = os.path.getsize(wheel)
    if size > MAX_WHEEL_BYTES:
        print(
            f"::error::{wheel} is {size / 1024 / 1024:.1f} MiB, over the "
            f"{MAX_WHEEL_BYTES // 1024 // 1024} MiB guard (PyPI's per-file limit is 100 MiB). "
            "Trim what the wheel bundles (see `make build-frontend`)."
        )
        sys.exit(1)

    print(f"✅ {wheel} bundles {count} frontend files (entrypoint present), {size / 1024 / 1024:.1f} MiB.")


if __name__ == "__main__":
    main()
