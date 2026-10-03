"""Guard: no FastAPI route may be ``async def`` without awaiting anything.

The API runs one uvicorn process with one event loop per Cloud Run instance.
An ``async def`` handler runs ON that loop, so every sync Firestore / GCS /
HTTP / email / LLM call inside it freezes every other request on the instance
(users see the "Reconnecting" / "servers unavailable" banner). A plain ``def``
handler is run by FastAPI in its threadpool instead. On 2026-10-03, 168
handlers were async-without-await; they were converted and this test keeps it
that way.

Fix a failure by making the handler a plain ``def`` (FastAPI then runs it in
the threadpool), or, if it genuinely needs to be async, ``await`` its blocking
work via ``asyncio.to_thread(...)``.
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROUTE_FILES = sorted((BACKEND / "api" / "routes").glob("*.py")) + [BACKEND / "main.py"]

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}


def _awaits_directly(fn: ast.AsyncFunctionDef) -> bool:
    """True if fn's own body (excluding nested function scopes) awaits."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


_LOOP_APIS = {"create_task", "ensure_future", "get_running_loop", "get_event_loop"}


def _uses_loop_api(fn) -> bool:
    """True if fn's own body calls a running-loop API (asyncio.create_task etc.).

    Such a handler genuinely needs to run ON the loop: in FastAPI's threadpool
    there is no running loop, so create_task raises (or, if the RuntimeError is
    swallowed, the work is silently dropped).
    """
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Attribute) and node.attr in _LOOP_APIS:
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _is_route(fn) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in HTTP_VERBS
        for d in fn.decorator_list
    )


def find_offenders(source: str) -> list[str]:
    """async routes that never await (and don't need the loop)."""
    tree = ast.parse(source)
    return [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and _is_route(n)
        and not _awaits_directly(n)
        and not _uses_loop_api(n)
    ]


def find_loopless_sync_routes(source: str) -> list[str]:
    """Plain-def routes that call a running-loop API — broken in the threadpool."""
    tree = ast.parse(source)
    return [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and _is_route(n) and _uses_loop_api(n)
    ]


def test_no_async_route_without_await():
    offenders = []
    for path in ROUTE_FILES:
        for name in find_offenders(path.read_text()):
            offenders.append(f"{path.relative_to(BACKEND.parent)}::{name}")
    assert not offenders, (
        "These routes are `async def` but never await, so their sync work blocks the "
        "event loop for every request. Make them plain `def` (FastAPI runs them in "
        "its threadpool) or await blocking work via asyncio.to_thread:\n  "
        + "\n  ".join(offenders)
    )


def test_no_sync_route_uses_running_loop_apis():
    offenders = []
    for path in ROUTE_FILES:
        for name in find_loopless_sync_routes(path.read_text()):
            offenders.append(f"{path.relative_to(BACKEND.parent)}::{name}")
    assert not offenders, (
        "These plain-`def` routes call asyncio.create_task/get_running_loop, which "
        "fails (or silently no-ops) in FastAPI's threadpool. Keep them `async def`:\n  "
        + "\n  ".join(offenders)
    )


def test_detector_flags_and_allows_correctly():
    src = '''
@router.get("/a")
async def bad(): return db.get()

@router.get("/b")
async def good():
    return await asyncio.to_thread(db.get)

@router.post("/c")
def sync_ok(): return db.get()

@router.post("/d")
async def nested_only():
    async def _bg():
        await x()
    tasks.add_task(_bg)

async def not_a_route(): return 1

@router.post("/e")
async def spawns_task():
    async def _bg():
        await x()
    asyncio.create_task(_bg())

@router.post("/f")
def sync_spawns_task():
    asyncio.get_running_loop().create_task(x())
'''
    assert sorted(find_offenders(src)) == ["bad", "nested_only"]
    assert find_loopless_sync_routes(src) == ["sync_spawns_task"]
