"""Deterministic Cloud Function source archives.

``pulumi.FileArchive(dir)`` zips and hashes *every* file in the directory. If
someone runs the function's tests first, ``__pycache__/*.pyc`` and
``.pytest_cache`` get packed into the deployed zip. The archive hash then
depends on which checkout last ran ``pulumi up``, so a clean checkout of main
shows phantom drift: the source object gets replaced and the function updated
with identical code. This happened to runner-manager on 2026-11-09.

``clean_source_archive`` packs only the real source files, so the hash depends
only on the code.
"""

from pathlib import Path

import pulumi

EXCLUDED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "venv", ".venv"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
EXCLUDED_NAMES = frozenset({".DS_Store"})


def source_files(source_dir: Path) -> dict[str, Path]:
    """Map archive-relative path -> file, skipping caches and OS junk."""
    files: dict[str, Path] = {}
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
            continue
        files[rel.as_posix()] = path
    if not files:
        raise ValueError(f"No source files found in {source_dir}")
    return files


def clean_source_archive(source_dir: Path) -> pulumi.AssetArchive:
    """An archive of ``source_dir``'s source files only (see module docstring)."""
    return pulumi.AssetArchive(
        {rel: pulumi.FileAsset(str(path)) for rel, path in source_files(source_dir).items()}
    )
