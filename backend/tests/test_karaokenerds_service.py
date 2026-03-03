"""
Tests for the karaokenerds service — HTML parsing, community detection, YouTube URL cleanup.

Ported from kjbox/kj-controller/tests/unit/test_karaoke_nerds.py.
"""

import pytest
from backend.services.karaokenerds_service import (
    parse_results,
    _clean_youtube_url,
    _parse_single_track,
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
