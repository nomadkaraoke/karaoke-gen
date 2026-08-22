"""
Tests for the karaokenerds service — HTML parsing, community detection, YouTube URL cleanup.

Ported from kjbox/kj-controller/tests/unit/test_karaoke_nerds.py.
"""

import pytest
from backend.services.karaokenerds_service import (
    parse_results,
    _clean_youtube_url,
    _parse_single_track,
    check_community_versions,
    check_community_versions_batch,
)
from bs4 import BeautifulSoup


# --- YouTube URL cleanup ---


def test_clean_youtube_url_strips_list_param():
    url = "https://www.youtube.com/watch?v=abc123&list=PLtest123&index=1"
    assert _clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123&index=1"


def test_clean_youtube_url_no_list_param():
    url = "https://www.youtube.com/watch?v=abc123"
    assert _clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123"


def test_clean_youtube_url_list_at_end():
    url = "https://www.youtube.com/watch?v=abc123&list=PLtest"
    assert _clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123"


# --- Single track parsing ---


TRACK_HTML_COMMUNITY = """
<li class="track list-group-item d-flex p-0">
  <a href="/Song/Dreams/Fleetwood-Mac/KV/">Karaoke Version</a>
  <div class="ml-auto">
    <a href="https://www.youtube.com/watch?v=mrZRURcb1cM&list=PLtest" target="_blank">
      <img class="web" src="/Content/Images/globe.svg">
    </a>
    <a href="/Song/Dreams/Fleetwood-Mac/KV/">
      <span class="badge badge-primary badge-pill">
        KV<img class="check" src="/Content/Images/check.svg" title="Global Karaoke Community">
      </span>
    </a>
  </div>
</li>
"""

TRACK_HTML_NO_COMMUNITY = """
<li class="track list-group-item d-flex p-0">
  <a href="/Song/Dreams/Fleetwood-Mac/SF/">Sunfly</a>
  <div class="ml-auto">
    <a href="https://www.youtube.com/watch?v=xyz789" target="_blank">
      <img class="web" src="/Content/Images/globe.svg">
    </a>
    <a href="/Song/Dreams/Fleetwood-Mac/SF/">
      <span class="badge badge-primary badge-pill">SF</span>
    </a>
  </div>
</li>
"""

TRACK_HTML_NO_YOUTUBE = """
<li class="track list-group-item d-flex p-0">
  <a href="/Song/Dreams/Fleetwood-Mac/AB/">Some Brand</a>
  <div class="ml-auto">
    <a href="/Song/Dreams/Fleetwood-Mac/AB/">
      <span class="badge badge-primary badge-pill">AB</span>
    </a>
  </div>
</li>
"""


def test_parse_single_track_community():
    soup = BeautifulSoup(TRACK_HTML_COMMUNITY, "html.parser")
    li = soup.find("li", class_="track")
    track = _parse_single_track(li)
    assert track is not None
    assert track["brand_name"] == "Karaoke Version"
    assert track["brand_code"] == "KV"
    assert track["is_community"] is True
    assert "youtube.com" in track["youtube_url"]
    # List param should be stripped
    assert "&list=" not in track["youtube_url"]


def test_parse_single_track_no_community():
    soup = BeautifulSoup(TRACK_HTML_NO_COMMUNITY, "html.parser")
    li = soup.find("li", class_="track")
    track = _parse_single_track(li)
    assert track is not None
    assert track["brand_name"] == "Sunfly"
    assert track["brand_code"] == "SF"
    assert track["is_community"] is False
    assert "youtube.com" in track["youtube_url"]


def test_parse_single_track_no_youtube_returns_none():
    soup = BeautifulSoup(TRACK_HTML_NO_YOUTUBE, "html.parser")
    li = soup.find("li", class_="track")
    track = _parse_single_track(li)
    assert track is None


# --- Full results parsing ---


FULL_RESULTS_HTML = """
<table>
  <tbody>
    <tr class="group">
      <td><a href="/Song/Dreams/Fleetwood-Mac/">Dreams</a></td>
      <td><a href="/Artist/Fleetwood-Mac/">Fleetwood Mac</a></td>
      <td><a class="details-link">2 Brands &gt;&gt;</a></td>
    </tr>
    <tr class="details">
      <td colspan="30">
        <ul class="list-group">
          <li class="track list-group-item d-flex p-0">
            <a href="/Song/Dreams/Fleetwood-Mac/KV/">Karaoke Version</a>
            <div class="ml-auto">
              <a href="https://www.youtube.com/watch?v=community1" target="_blank">
                <img class="web" src="/Content/Images/globe.svg">
              </a>
              <a href="/Song/Dreams/Fleetwood-Mac/KV/">
                <span class="badge badge-primary badge-pill">
                  KV<img class="check" src="/Content/Images/check.svg" title="Global Karaoke Community">
                </span>
              </a>
            </div>
          </li>
          <li class="track list-group-item d-flex p-0">
            <a href="/Song/Dreams/Fleetwood-Mac/SF/">Sunfly</a>
            <div class="ml-auto">
              <a href="https://www.youtube.com/watch?v=noncommunity1" target="_blank">
                <img class="web" src="/Content/Images/globe.svg">
              </a>
              <a href="/Song/Dreams/Fleetwood-Mac/SF/">
                <span class="badge badge-primary badge-pill">SF</span>
              </a>
            </div>
          </li>
        </ul>
      </td>
    </tr>
    <tr class="group">
      <td><a href="/Song/The-Chain/Fleetwood-Mac/">The Chain</a></td>
      <td><a href="/Artist/Fleetwood-Mac/">Fleetwood Mac</a></td>
      <td><a class="details-link">1 Brand &gt;&gt;</a></td>
    </tr>
    <tr class="details">
      <td colspan="30">
        <ul class="list-group">
          <li class="track list-group-item d-flex p-0">
            <a href="/Song/The-Chain/Fleetwood-Mac/KFN/">Karafun</a>
            <div class="ml-auto">
              <a href="https://www.youtube.com/watch?v=chain1" target="_blank">
                <img class="web" src="/Content/Images/globe.svg">
              </a>
              <a href="/Song/The-Chain/Fleetwood-Mac/KFN/">
                <span class="badge badge-primary badge-pill">KFN</span>
              </a>
            </div>
          </li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>
"""


def test_parse_results_full():
    songs = parse_results(FULL_RESULTS_HTML)
    assert len(songs) == 2

    # First song: Dreams
    assert songs[0]["title"] == "Dreams"
    assert songs[0]["artist"] == "Fleetwood Mac"
    assert len(songs[0]["tracks"]) == 2

    # First track is community
    assert songs[0]["tracks"][0]["is_community"] is True
    assert songs[0]["tracks"][0]["brand_code"] == "KV"

    # Second track is not community
    assert songs[0]["tracks"][1]["is_community"] is False
    assert songs[0]["tracks"][1]["brand_code"] == "SF"

    # Second song: The Chain
    assert songs[1]["title"] == "The Chain"
    assert songs[1]["artist"] == "Fleetwood Mac"
    assert len(songs[1]["tracks"]) == 1
    assert songs[1]["tracks"][0]["is_community"] is False


def test_parse_results_empty_html():
    assert parse_results("") == []
    assert parse_results("<html></html>") == []
    assert parse_results("<table></table>") == []


def test_parse_results_no_table():
    assert parse_results("<div>No results</div>") == []


# --- Batch availability: per-version data with YouTube URLs (Bulk Mode) ---


@pytest.mark.asyncio
async def test_batch_returns_versions_with_urls_deduped_by_brand(monkeypatch):
    """Each result carries `versions: [{brand, url}]` (community only), deduped by
    brand keeping the first URL, alongside the existing brands/brand_count."""

    async def fake_check(artist, title):
        return {
            "has_community": True,
            "songs": [
                {
                    "title": title,
                    "artist": artist,
                    "community_tracks": [
                        {"brand_name": "SNDL Karaoke", "brand_code": "SNDL",
                         "youtube_url": "https://www.youtube.com/watch?v=aaa", "is_community": True},
                        {"brand_name": "Nomad Karaoke", "brand_code": "NK",
                         "youtube_url": "https://www.youtube.com/watch?v=bbb", "is_community": True},
                        # Duplicate brand — should be collapsed, keeping the first URL.
                        {"brand_name": "SNDL Karaoke", "brand_code": "SNDL",
                         "youtube_url": "https://www.youtube.com/watch?v=ccc", "is_community": True},
                    ],
                }
            ],
            "best_youtube_url": "https://www.youtube.com/watch?v=aaa",
        }

    monkeypatch.setattr(
        "backend.services.karaokenerds_service.check_community_versions", fake_check
    )

    results = await check_community_versions_batch(
        [{"artist": "ABBA", "title": "Dancing Queen"}]
    )

    assert len(results) == 1
    r = results[0]
    assert r["available"] is True
    assert r["versions"] == [
        {"brand": "SNDL Karaoke", "url": "https://www.youtube.com/watch?v=aaa"},
        {"brand": "Nomad Karaoke", "url": "https://www.youtube.com/watch?v=bbb"},
    ]
    # Back-compat retained.
    assert r["brands"] == ["SNDL Karaoke", "Nomad Karaoke"]
    assert r["brand_count"] == 2


@pytest.mark.asyncio
async def test_batch_empty_song_has_empty_versions():
    results = await check_community_versions_batch([{"artist": "", "title": ""}])
    assert results[0]["available"] is False
    assert results[0]["versions"] == []


# --- check_community_versions: overlong-query fallback (KaraokeNerds term cap) ---


def _community_song(title, artist, brand="Nomad Karaoke", url="https://youtu.be/x"):
    return {
        "title": title,
        "artist": artist,
        "tracks": [
            {"brand_name": brand, "brand_code": "NK", "youtube_url": url, "is_community": True}
        ],
    }


@pytest.fixture(autouse=True)
def _clear_community_cache():
    """check_community_versions caches in-process; isolate each test."""
    from backend.services import karaokenerds_service as svc
    svc._cache.clear()
    yield
    svc._cache.clear()


@pytest.mark.asyncio
async def test_overlong_query_falls_back_to_title_only(monkeypatch):
    """An overlong "artist title" query that returns nothing retries title-only
    and matches by artist (the ABBA 'I Do, I Do, I Do, I Do, I Do' bug)."""
    title = "I Do, I Do, I Do, I Do, I Do"
    calls = []

    async def fake_search(query):
        calls.append(query)
        if query == f"ABBA {title}":
            return []  # combined query is too long -> KaraokeNerds returns nothing
        if query == title:
            return [_community_song("I Do I Do I Do I Do I Do", "ABBA")]
        return []

    monkeypatch.setattr(
        "backend.services.karaokenerds_service._search_songs", fake_search
    )

    result = await check_community_versions("ABBA", title)

    assert result["has_community"] is True
    assert result["songs"][0]["artist"] == "ABBA"
    assert calls == [f"ABBA {title}", title]  # combined first, then title-only fallback


@pytest.mark.asyncio
async def test_fallback_filters_out_wrong_artist(monkeypatch):
    """Title-only fallback is broader, so same-titled songs by other artists
    must be discarded — no false positive."""
    title = "I Do, I Do, I Do, I Do, I Do"

    async def fake_search(query):
        if query == title:
            return [_community_song("I Do I Do I Do I Do I Do", "Some Other Band")]
        return []

    monkeypatch.setattr(
        "backend.services.karaokenerds_service._search_songs", fake_search
    )

    result = await check_community_versions("ABBA", title)

    assert result["has_community"] is False
    assert result["songs"] == []


@pytest.mark.asyncio
async def test_short_empty_query_does_not_fall_back(monkeypatch):
    """A short query that legitimately returns nothing must NOT trigger a second
    request (avoids doubling load for songs with no community version)."""
    calls = []

    async def fake_search(query):
        calls.append(query)
        return []

    monkeypatch.setattr(
        "backend.services.karaokenerds_service._search_songs", fake_search
    )

    result = await check_community_versions("ABBA", "Dancing Queen")

    assert result["has_community"] is False
    assert calls == ["ABBA Dancing Queen"]  # no fallback request


@pytest.mark.asyncio
async def test_nonempty_combined_result_skips_fallback(monkeypatch):
    """If the combined query returns results, never fall back even when long."""
    title = "I Do, I Do, I Do, I Do, I Do"
    calls = []

    async def fake_search(query):
        calls.append(query)
        return [_community_song("I Do I Do I Do I Do I Do", "ABBA")]

    monkeypatch.setattr(
        "backend.services.karaokenerds_service._search_songs", fake_search
    )

    result = await check_community_versions("ABBA", title)

    assert result["has_community"] is True
    assert calls == [f"ABBA {title}"]  # only the combined query ran
