"""Tests for modules/source_archive.py (run: pytest infrastructure/modules/test_source_archive.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from source_archive import clean_source_archive, source_files  # noqa: E402


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_skips_caches_and_os_junk(tmp_path):
    _write(tmp_path / "main.py")
    _write(tmp_path / "requirements.txt")
    _write(tmp_path / "pkg" / "helper.py")
    _write(tmp_path / "__pycache__" / "main.cpython-313.pyc")
    _write(tmp_path / "pkg" / "__pycache__" / "helper.cpython-313.pyc")
    _write(tmp_path / ".pytest_cache" / "v" / "cache" / "lastfailed")
    _write(tmp_path / "stray.pyc")
    _write(tmp_path / ".DS_Store")

    assert list(source_files(tmp_path)) == ["main.py", "pkg/helper.py", "requirements.txt"]


def test_same_files_regardless_of_test_run_leftovers(tmp_path):
    clean = tmp_path / "clean"
    dirty = tmp_path / "dirty"
    for root in (clean, dirty):
        _write(root / "main.py", "print('hi')")
    _write(dirty / "__pycache__" / "main.cpython-313.pyc", "bytecode")

    assert list(source_files(clean)) == list(source_files(dirty))


def test_archive_holds_file_assets(tmp_path):
    _write(tmp_path / "main.py")
    archive = clean_source_archive(tmp_path)
    assert set(archive.assets) == {"main.py"}


def test_empty_dir_raises(tmp_path):
    _write(tmp_path / "__pycache__" / "only.pyc")
    with pytest.raises(ValueError):
        source_files(tmp_path)
