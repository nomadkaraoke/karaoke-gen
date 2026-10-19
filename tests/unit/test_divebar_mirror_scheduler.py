"""Guard: divebar-mirror Cloud Scheduler jobs must wait for the function to finish.

Cloud Scheduler's default HTTP attempt_deadline is 180s, but a divebar-mirror run
takes 360-525s. The scheduler gave up on a still-running run and retried it
(retry_count=2); the retries queued behind it on the max_instance_count=1
function, and the third concurrent run hit the 512 MiB limit (4 OOM alerts in 30
days). The deadline has to outlast the function timeout so a slow run is never
retried while it is still working.
"""
from __future__ import annotations

import ast
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "infrastructure" / "modules" / "divebar_mirror.py"


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _calls(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


def _function_timeout_s(tree: ast.AST) -> int:
    (svc,) = _calls(tree, "FunctionServiceConfigArgs")
    return ast.literal_eval(_kwargs(svc)["timeout_seconds"])


def _deadline(tree: ast.Module, node: ast.expr) -> str:
    """Literal value, or the module-level constant the job references."""
    if isinstance(node, ast.Name):
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == node.id for t in stmt.targets
            ):
                return ast.literal_eval(stmt.value)
        raise AssertionError(f"constant {node.id} not found")
    return ast.literal_eval(node)


def test_every_scheduler_job_outlasts_the_function_timeout():
    tree = ast.parse(MODULE.read_text())
    timeout = _function_timeout_s(tree)
    jobs = [c for c in _calls(tree, "Job") if "http_target" in _kwargs(c)]
    names = [ast.literal_eval(_kwargs(j)["name"]) for j in jobs]
    assert names == ["divebar-mirror-daily", "divebar-mirror-refresh"]
    for job, name in zip(jobs, names):
        kw = _kwargs(job)
        assert "attempt_deadline" in kw, f"{name}: no attempt_deadline (default 180s retries a running sync)"
        deadline = _deadline(tree, kw["attempt_deadline"])
        assert deadline.endswith("s")
        assert int(deadline[:-1]) > timeout, f"{name}: {deadline} <= function timeout {timeout}s"
