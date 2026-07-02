import asyncio

from backend.services.parse_titles import ai
from backend.services.parse_titles import service


def test_settings_parse_titles_defaults():
    from backend.config import Settings
    s = Settings()
    assert s.parse_titles_enabled is True
    assert s.parse_titles_model == "gemini-3.5-flash"
    assert s.parse_titles_timeout_ms == 20000
    assert s.parse_titles_max_items == 200


def test_build_prompts_lists_items_with_ids():
    system, user = ai.build_prompts(
        [{"id": "a", "filename": "Santeria - Sublime _ Karaoke _ KaraFun.mp4",
          "channel": "KaraFun", "source": "youtube"}]
    )
    assert "artist" in system.lower() and "title" in system.lower()
    assert "id='a'" in user or '"a"' in user or "id=a" in user  # id must be echoed
    assert "Santeria" in user


def test_parse_map_from_response_aligns_by_id_and_fills_misses():
    items = [{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}]
    data = {"results": [{"id": "a", "artist": "Sublime", "title": "Santeria",
                         "confidence": 0.9}]}
    out = ai.parse_map_from_response(data, items)
    assert {r["id"] for r in out} == {"a", "b"}
    a = next(r for r in out if r["id"] == "a")
    assert (a["artist"], a["title"]) == ("Sublime", "Santeria")
    b = next(r for r in out if r["id"] == "b")
    assert b["artist"] == "" and b["title"] == "" and b["confidence"] == 0.0


def test_ai_parse_uses_injected_generate():
    items = [{"id": "a", "filename": "Bella Kay - iloveit (Karaoke Version).mp4"}]

    async def fake_generate(model, system, user):
        return {"results": [{"id": "a", "artist": "Bella Kay",
                             "title": "iloveit", "confidence": 0.82}]}

    out = asyncio.run(ai.ai_parse(items, generate=fake_generate))
    assert out[0]["artist"] == "Bella Kay" and out[0]["confidence"] == 0.82


def test_parse_map_handles_garbage_response():
    items = [{"id": "a", "filename": "x"}]
    assert ai.parse_map_from_response("not a dict", items) == [
        {"id": "a", "artist": "", "title": "", "confidence": 0.0}
    ]


def test_parse_titles_disabled_returns_blanks(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", False)
    out = asyncio.run(service.parse_titles([{"id": "a", "filename": "x"}]))
    assert out == [{"id": "a", "artist": "", "title": "", "confidence": 0.0}]


def test_parse_titles_degrades_on_generate_error(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)

    async def boom(model, system, user):
        raise RuntimeError("vertex down")

    out = asyncio.run(service.parse_titles(
        [{"id": "a", "filename": "x"}], generate=boom))
    assert out == [{"id": "a", "artist": "", "title": "", "confidence": 0.0}]


def test_parse_titles_happy_path(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)

    async def gen(model, system, user):
        return {"results": [{"id": "a", "artist": "Queen",
                             "title": "Bohemian Rhapsody", "confidence": 0.95}]}

    out = asyncio.run(service.parse_titles([{"id": "a", "filename": "x"}], generate=gen))
    assert out[0]["artist"] == "Queen" and out[0]["confidence"] == 0.95


def test_parse_titles_empty_returns_empty():
    assert asyncio.run(service.parse_titles([])) == []
