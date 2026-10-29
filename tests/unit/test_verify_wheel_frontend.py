"""Tests for scripts/verify_wheel_frontend.py (frontend-bundled + PyPI size guard)."""

import importlib.util
import os
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_wheel_frontend", Path(__file__).resolve().parents[2] / "scripts" / "verify_wheel_frontend.py"
)
verify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify)


def _make_wheel(tmp_path: Path, n_files: int = 60, with_entry: bool = True) -> Path:
    wheel = tmp_path / "karaoke_gen-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        if with_entry:
            zf.writestr(verify.REQUIRED_ENTRY, "<html></html>")
        for i in range(n_files):
            zf.writestr(f"{verify.FRONTEND_PREFIX}en/page{i}/index.html", "x")
    return wheel


def _run(monkeypatch, wheel: Path) -> None:
    monkeypatch.setattr("sys.argv", ["verify_wheel_frontend.py", str(wheel)])
    verify.main()


def test_passes_for_bundled_frontend_under_size_guard(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, _make_wheel(tmp_path))
    assert "✅" in capsys.readouterr().out


def test_fails_without_entrypoint(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, _make_wheel(tmp_path, with_entry=False))
    assert exc.value.code == 1


def test_fails_with_too_few_frontend_files(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, _make_wheel(tmp_path, n_files=3))
    assert exc.value.code == 1


def test_fails_when_wheel_exceeds_pypi_size_guard(tmp_path, monkeypatch, capsys):
    wheel = _make_wheel(tmp_path)
    real_getsize = os.path.getsize
    monkeypatch.setattr(
        verify.os.path, "getsize",
        lambda p: verify.MAX_WHEEL_BYTES + 1 if str(p) == str(wheel) else real_getsize(p),
    )
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, wheel)
    assert exc.value.code == 1
    assert "PyPI" in capsys.readouterr().out


def test_size_guard_leaves_headroom_below_pypi_limit():
    assert verify.MAX_WHEEL_BYTES < 100 * 1024 * 1024
