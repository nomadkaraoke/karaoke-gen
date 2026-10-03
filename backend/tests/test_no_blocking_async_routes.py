"""Guard: no FastAPI route may be ``async def`` without awaiting anything.

The API runs one uvicorn process with one event loop per Cloud Run instance.
An ``async def`` handler runs ON that loop, so every sync Firestore / GCS /
HTTP / email / LLM call inside it freezes every other request on the instance
(users see the "Reconnecting" / "servers unavailable" banner). A plain ``def``
handler is run by FastAPI in its threadpool instead. On 2026-10-03, 168
handlers were async-without-await; 161 were converted (trivial no-call ones like health checks stay async) and this test keeps it
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


def _is_trivial(fn) -> bool:
    """No calls at all in the body (e.g. a health check returning a literal dict) —
    can't block, and keeping it async means it never waits for a threadpool slot."""
    return not any(isinstance(c, ast.Call) for stmt in fn.body for c in ast.walk(stmt))


def find_offenders(source: str) -> list[str]:
    """async routes that never await (and don't need the loop, and aren't trivial)."""
    tree = ast.parse(source)
    return [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and _is_route(n)
        and not _awaits_directly(n)
        and not _uses_loop_api(n)
        and not _is_trivial(n)
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


def find_awaited_sync_routes(source: str) -> list[str]:
    """`await some_sync_route(...)` within the same module — a TypeError at runtime
    (awaiting the return value), usually swallowed by a broad except."""
    tree = ast.parse(source)
    sync_routes = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and _is_route(n)
    }
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            fn = node.value.func
            # Bare names only: `await svc.health_check()` is a different object's
            # (async) method that merely shares a route's name.
            if isinstance(fn, ast.Name) and fn.id in sync_routes:
                hits.append(fn.id)
    return hits


def test_no_await_of_sync_route_handlers():
    offenders = []
    for path in ROUTE_FILES:
        for name in find_awaited_sync_routes(path.read_text()):
            offenders.append(f"{path.relative_to(BACKEND.parent)}::await {name}(...)")
    assert not offenders, (
        "These code paths `await` a plain-`def` route handler (TypeError at runtime). "
        "Call it via `await asyncio.to_thread(handler, ...)` instead:\n  "
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

@router.get("/health")
async def trivial(): return {"status": "ok"}

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
    caller = src + '''
async def helper():
    await sync_ok()
    await asyncio.to_thread(sync_ok)
    await client.sync_ok()
'''
    assert find_awaited_sync_routes(caller) == ["sync_ok"]
