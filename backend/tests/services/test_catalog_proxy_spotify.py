"""catalog_proxy_service.spotify_search: gen -> decide's partner Spotify search."""
import httpx
import pytest

from backend.services import catalog_proxy_service as proxy


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    proxy._cache.clear()
    monkeypatch.setattr(proxy.settings, "decide_partner_secret", "s3cret", raising=False)
    monkeypatch.setattr(proxy.settings, "karaoke_decide_api_url", "https://decide.test", raising=False)
    yield
    proxy._cache.clear()


def _mock(monkeypatch, handler):
    real = httpx.AsyncClient
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(proxy.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(wrapped), **kw))
    return seen


_BODY = {"available": True, "tracks": [{"artist_name": "Cole Phillips", "track_name": "Roll of Dice"}]}


@pytest.mark.asyncio
async def test_sends_secret_and_returns_tracks(monkeypatch):
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json=_BODY))
    tracks = await proxy.spotify_search("  Cole Phillips   Roll of dice ")
    assert tracks == _BODY["tracks"]
    req = seen[0]
    assert req.url.path == "/api/partner/gen/spotify-search"
    assert req.url.params["q"] == "Cole Phillips Roll of dice"
    assert req.url.params["limit"] == "10"
    assert req.headers["X-Partner-Secret"] == "s3cret"


@pytest.mark.asyncio
async def test_results_are_cached(monkeypatch):
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json=_BODY))
    await proxy.spotify_search("Cole Phillips Roll of dice")
    await proxy.spotify_search("cole phillips roll of dice")
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"available": False, "tracks": []}),   # decide's breaker open
    httpx.Response(401, json={"detail": "partner_auth"}),
    httpx.Response(503, json={"detail": "partner_disabled"}),
    httpx.Response(200, json=["not", "a", "dict"]),
])
async def test_no_evidence_is_none_and_not_cached(monkeypatch, response):
    seen = _mock(monkeypatch, lambda r: response)
    assert await proxy.spotify_search("some song") is None
    assert await proxy.spotify_search("some song") is None
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_network_error_is_none(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    _mock(monkeypatch, boom)
    assert await proxy.spotify_search("some song") is None


@pytest.mark.asyncio
async def test_no_secret_or_short_query_skips_the_call(monkeypatch):
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json=_BODY))
    assert await proxy.spotify_search("x") is None
    monkeypatch.setattr(proxy.settings, "decide_partner_secret", "", raising=False)
    assert await proxy.spotify_search("some song") is None
    assert seen == []
