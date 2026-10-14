"""Unit tests for the CI PyPI publish gate (scripts/pypi_publish_gate.py).

The gate only publishes to PyPI when something CLI users get changed since the
latest version on PyPI. The scripts aren't part of the installed package, so we
load them by path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # register before exec so @dataclass resolves
    spec.loader.exec_module(mod)
    return mod


gate = _load("pypi_publish_gate", REPO / "scripts" / "pypi_publish_gate.py")
# Reuse the encoding-worker gate's import-closure helpers.
worker_paths = _load("encoding_worker_code_paths", REPO / "backend" / "tests" / "test_encoding_worker_code_paths.py")

PYPROJECT = '[tool.poetry]\nname = "karaoke-gen"\nversion = "{v}"\n\n[tool.poetry.dependencies]\n{deps}\n'


class FakeGit:
    """Stands in for `git`: canned diff output and pyproject.toml per ref."""

    def __init__(self, changed=(), old_deps='requests = ">=2"', new_deps='requests = ">=2"', fail=False):
        self.changed = list(changed)
        self.pyproject = {"base": PYPROJECT.format(v="1.0.0", deps=old_deps), "HEAD": PYPROJECT.format(v="1.0.5", deps=new_deps)}
        self.fail = fail
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if self.fail:
            raise RuntimeError("tag not found")
        if args[0] == "fetch":
            return ""
        if args[0] == "diff":
            return "\n".join(self.changed) + "\n"
        if args[0] == "show":
            return self.pyproject["HEAD" if args[1].startswith("HEAD") else "base"]
        raise AssertionError(f"unexpected git call {args}")


def _decide(git=None, *, current="1.0.5", pypi="1.0.0", force=False, message="fix: something"):
    def get_pypi():
        if isinstance(pypi, Exception):
            raise pypi
        return pypi

    return gate.decide(
        current_version=current,
        force=force,
        commit_message=message,
        pathspecs=["karaoke_gen/"],
        get_pypi_version=get_pypi,
        git=git or FakeGit(),
    )


def test_skips_when_nothing_cli_affecting_changed():
    d = _decide(FakeGit(changed=[]))
    assert d.publish is False
    assert "v1.0.0" in d.reason


def test_publishes_when_a_gated_path_changed():
    git = FakeGit(changed=["karaoke_gen/utils/gen_cli.py"])
    d = _decide(git)
    assert d.publish is True
    assert "karaoke_gen/utils/gen_cli.py" in d.reason
    # Diff base is the version on PyPI, restricted to the gated paths.
    assert ("diff", "--name-only", "v1.0.0", "HEAD", "--", "karaoke_gen/") in git.calls


def test_version_bump_alone_does_not_publish():
    # FakeGit's pyproject differs only in the version line (1.0.0 → 1.0.5).
    assert _decide(FakeGit()).publish is False


def test_pyproject_dependency_change_publishes():
    d = _decide(FakeGit(new_deps='requests = ">=3"'))
    assert d.publish is True
    assert "pyproject.toml" in d.reason


def test_already_on_pypi_skips_without_touching_git():
    git = FakeGit(changed=["karaoke_gen/x.py"])
    d = _decide(git, current="1.0.0", pypi="1.0.0")
    assert d.publish is False
    assert git.calls == []


@pytest.mark.parametrize("kwargs", [{"force": True}, {"message": "fix: cli flag [publish-pypi] (#12)\n\nbody"}])
def test_force_publishes_even_without_changes(kwargs):
    git = FakeGit(changed=[])
    assert _decide(git, **kwargs).publish is True
    assert git.calls == []


def test_marker_in_commit_body_does_not_force():
    # A squash merge's body is the PR description, which may just *mention* the
    # marker (PR #1145's did, forcing an unwanted publish).
    git = FakeGit(changed=[])
    d = _decide(git, message="ci: gate PyPI publish (#1145)\n\nForce with `[publish-pypi]` in the PR title.")
    assert d.publish is False


def test_fails_open_when_pypi_unreachable():
    d = _decide(pypi=OSError("timeout"))
    assert d.publish is True
    assert "PyPI" in d.reason


def test_fails_open_when_git_diff_fails():
    d = _decide(FakeGit(fail=True))
    assert d.publish is True
    assert "v1.0.0" in d.reason


def test_strip_version_line_only_drops_the_package_version():
    text = '[tool.poetry]\nname = "x"\nversion = "1.2.3"\ntorch = ">=2"\n'
    assert gate.strip_version_line(text) == '[tool.poetry]\nname = "x"\ntorch = ">=2"'


def test_dependency_table_version_change_still_counts():
    # [tool.poetry.dependencies.<name>] tables have their own `version =` lines.
    old = '[tool.poetry]\nversion = "1.0.0"\n\n[tool.poetry.dependencies.pytest]\nversion = ">=7.0"\n'
    new = '[tool.poetry]\nversion = "1.0.5"\n\n[tool.poetry.dependencies.pytest]\nversion = ">=8.0"\n'
    assert gate.strip_version_line(old) != gate.strip_version_line(new)
    assert gate.strip_version_line(old) == gate.strip_version_line(old.replace("1.0.0", "9.9.9"))


def test_strip_version_line_on_the_real_pyproject():
    text = (REPO / "pyproject.toml").read_text()
    stripped = gate.strip_version_line(text)
    assert stripped.count("version =") == text.count("version =") - 1


def test_writes_github_outputs(tmp_path, monkeypatch):
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    gate._write_github_outputs(gate.Decision(False, "no changes"))
    assert out.read_text() == "publish=false\nreason=no changes\n"
    assert "skipped" in summary.read_text()


# --- the path list itself ---------------------------------------------------


def test_paths_file_is_well_formed():
    specs = gate.load_pathspecs()
    assert "karaoke_gen/" in specs
    # pyproject.toml is bumped on every PR and is compared separately with the
    # version line stripped; poetry.lock doesn't ship in the wheel.
    assert "pyproject.toml" not in specs
    assert "poetry.lock" not in specs
    assert not any(s.startswith("frontend") for s in specs)
    for s in specs:
        assert (REPO / s.rstrip("/")).exists(), f"stale path in {gate.PATHS_FILE.name}: {s}"


def _cli_backend_closure():
    seen, queue, backend_files = set(), list((REPO / "karaoke_gen").rglob("*.py")), set()
    while queue:
        f = queue.pop()
        if f in seen:
            continue
        seen.add(f)
        for mod in worker_paths._backend_imports(f):
            mf = worker_paths._module_file(mod)
            if not mf:
                continue
            parts = mod.split(".")
            pkg_inits = [worker_paths._module_file(".".join(parts[:i])) for i in range(1, len(parts))]
            for dep in [mf, *pkg_inits]:
                if dep and dep not in seen:
                    backend_files.add(dep)
                    queue.append(dep)
    return backend_files


# Imported by the CLI but deliberately not gated: see the comment in
# scripts/pypi_publish_paths.txt (cloud settings, changed on most backend PRs).
UNGATED_CLI_IMPORTS = {"backend/config.py"}


def test_every_backend_module_the_cli_imports_is_gated():
    specs = gate.load_pathspecs()
    closure = {str(f.relative_to(REPO)) for f in _cli_backend_closure()}
    assert UNGATED_CLI_IMPORTS <= closure, "stale UNGATED_CLI_IMPORTS entry"
    missing = sorted(p for p in closure - UNGATED_CLI_IMPORTS if not worker_paths._covered(p, specs))
    assert not missing, (
        f"karaoke_gen imports backend modules not listed in {gate.PATHS_FILE.relative_to(REPO)} — "
        f"add them so CI publishes the CLI when they change: {missing}"
    )
