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


# --- auto-chunking: large batches must not ride one >20s Gemini call ---

def _items(n):
    return [{"id": str(i), "filename": f"file {i}.mp4"} for i in range(n)]


def _ok_results(model, system, user):
    """Echo one parsed result per '- id=' line in the user prompt."""
    ids = [line.split("id='", 1)[1].split("'", 1)[0]
           for line in user.splitlines() if line.startswith("- id=")]
    return {"results": [{"id": i, "artist": f"A{i}", "title": f"T{i}",
                         "confidence": 0.9} for i in ids]}


def test_settings_parse_titles_chunk_size_default():
    from backend.config import Settings
    assert Settings().parse_titles_chunk_size == 10


def test_parse_titles_chunks_large_batches(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)
    calls = []

    async def gen(model, system, user):
        n = sum(1 for line in user.splitlines() if line.startswith("- id="))
        calls.append(n)
        return _ok_results(model, system, user)

    out = asyncio.run(service.parse_titles(_items(25), generate=gen))
    assert sorted(calls, reverse=True) == [10, 10, 5]   # 25 items -> 3 Gemini calls
    assert [r["id"] for r in out] == [str(i) for i in range(25)]  # id-aligned
    assert all(r["artist"] == f'A{r["id"]}' for r in out)


def test_parse_titles_chunk_failure_degrades_only_that_chunk(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)

    async def gen(model, system, user):
        if "id='10'" in user:  # the middle chunk (items 10-19)
            raise RuntimeError("vertex timeout")
        return _ok_results(model, system, user)

    out = asyncio.run(service.parse_titles(_items(25), generate=gen))
    assert out[0]["artist"] == "A0" and out[24]["artist"] == "A24"
    assert all(out[i]["artist"] == "" and out[i]["confidence"] == 0.0
               for i in range(10, 20))                   # only that chunk blank
    assert [r["id"] for r in out] == [str(i) for i in range(25)]


def test_parse_titles_chunks_run_concurrently(monkeypatch):
    """The chunks must be issued in parallel — a 100-item batch shouldn't pay
    10 sequential Gemini round-trips."""
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)
    started = 0
    all_started = asyncio.Event()

    async def gen(model, system, user):
        nonlocal started
        started += 1
        if started == 3:
            all_started.set()
        # Sequential execution would deadlock here on the first chunk.
        await asyncio.wait_for(all_started.wait(), timeout=5)
        return _ok_results(model, system, user)

    out = asyncio.run(service.parse_titles(_items(25), generate=gen))
    assert len(out) == 25 and out[0]["artist"] == "A0"


def test_parse_titles_respects_configured_chunk_size(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)
    monkeypatch.setattr(settings, "parse_titles_chunk_size", 4, raising=False)
    calls = []

    async def gen(model, system, user):
        calls.append(sum(1 for line in user.splitlines() if line.startswith("- id=")))
        return _ok_results(model, system, user)

    asyncio.run(service.parse_titles(_items(10), generate=gen))
    assert sorted(calls, reverse=True) == [4, 4, 2]
