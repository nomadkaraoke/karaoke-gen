"""Guard for the CI encoding-worker deploy gate.

CI only blue-green redeploys the GCE encoding worker when a path listed in
infrastructure/encoding-worker/worker_code_paths.txt changed since the serving
worker's version. If the worker starts importing a backend module that is NOT
listed, changes to it would ship to the worker only on its next cold boot and
without the pre-promote test encode. This test derives the worker's import
closure (every ``backend.*`` module reachable from the worker entrypoint and the
karaoke_gen package) and asserts each one is covered by the path list.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATHS_FILE = REPO / "infrastructure" / "encoding-worker" / "worker_code_paths.txt"


def _pathspecs():
    specs = []
    for line in PATHS_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            specs.append(line)
    return specs


def _covered(rel_path: str, specs) -> bool:
    return any(rel_path == s or (s.endswith("/") and rel_path.startswith(s)) for s in specs)


def _backend_imports(py_file: Path):
    """All ``backend.*`` module names imported anywhere in the file (incl. lazily)."""
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "backend" or node.module.startswith("backend."):
                mods.add(node.module)
                for alias in node.names:  # `from backend.services import x` → module x
                    mods.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("backend."):
                    mods.add(alias.name)
    return mods


def _module_file(mod: str):
    base = REPO / Path(*mod.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None  # a name imported from a module, not a module itself


def _worker_closure():
    roots = list((REPO / "karaoke_gen").rglob("*.py"))
    roots += list((REPO / "backend" / "services" / "gce_encoding").rglob("*.py"))
    seen_files = set()
    queue = list(roots)
    backend_files = set()
    while queue:
        f = queue.pop()
        if f in seen_files:
            continue
        seen_files.add(f)
        for mod in _backend_imports(f):
            mf = _module_file(mod)
            if not mf:
                continue
            # Importing backend.a.b also executes backend/__init__.py and
            # backend/a/__init__.py — they're part of the worker's code too.
            parts = mod.split(".")
            pkg_inits = [_module_file(".".join(parts[:i])) for i in range(1, len(parts))]
            for dep in [mf, *pkg_inits]:
                if dep and dep not in seen_files:
                    backend_files.add(dep)
                    queue.append(dep)
    return backend_files


def test_worker_code_paths_file_is_well_formed():
    specs = _pathspecs()
    assert "karaoke_gen/" in specs
    assert "backend/services/gce_encoding/" in specs
    # pyproject.toml changes on EVERY PR (version bump) — listing it would make
    # the gate fire on every push, defeating it. Dependency changes are caught
    # via poetry.lock.
    assert "pyproject.toml" not in specs
    assert "poetry.lock" in specs
    for s in specs:
        assert (REPO / s.rstrip("/")).exists(), f"stale path in {PATHS_FILE.name}: {s}"


def test_every_backend_module_the_worker_imports_is_gated():
    specs = _pathspecs()
    missing = sorted(
        str(f.relative_to(REPO)) for f in _worker_closure()
        if not _covered(str(f.relative_to(REPO)), specs)
    )
    assert not missing, (
        "The encoding worker imports backend modules not listed in "
        f"{PATHS_FILE.relative_to(REPO)} — add them so CI redeploys the worker "
        f"when they change: {missing}"
    )
