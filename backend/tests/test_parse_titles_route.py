from fastapi.testclient import TestClient

from backend.main import app
from backend.api.dependencies import require_admin


def _admin_result():
    from backend.services.auth_service import AuthResult, UserType
    return AuthResult(is_valid=True, user_type=UserType.ADMIN, remaining_uses=-1,
                      message="ok", user_email="a@nomadkaraoke.com", is_admin=True)


def test_parse_route_happy_path(monkeypatch):
    # Self-contained: set our own admin override rather than relying on the
    # conftest autouse fixture (which can be disturbed by earlier tests in a
    # full-suite run and leave the endpoint returning 401).
    from backend.api.routes import parse_titles as route

    async def fake_parse(items, **kw):
        return [{"id": str(it["id"]), "artist": "Queen",
                 "title": "Bohemian Rhapsody", "confidence": 0.9} for it in items]

    monkeypatch.setattr(route, "parse_titles", fake_parse)
    original = app.dependency_overrides.get(require_admin)
    app.dependency_overrides[require_admin] = _admin_result
    try:
        client = TestClient(app)
        resp = client.post("/api/parse-karaoke-titles", json={
            "items": [{"id": "1", "filename": "x.mp4", "source": "youtube"}]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"][0]["id"] == "1"
        assert body["results"][0]["artist"] == "Queen"
    finally:
        if original is not None:
            app.dependency_overrides[require_admin] = original
        else:
            app.dependency_overrides.pop(require_admin, None)


def test_parse_route_requires_admin():
    """The route is gated by require_admin: a non-admin dependency -> 403."""
    original = app.dependency_overrides.get(require_admin)

    def non_admin():
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = non_admin
    try:
        client = TestClient(app)
        resp = client.post("/api/parse-karaoke-titles",
                           json={"items": [{"id": "1", "filename": "x"}]})
        assert resp.status_code == 403
    finally:
        if original is not None:
            app.dependency_overrides[require_admin] = original
        else:
            app.dependency_overrides.pop(require_admin, None)
