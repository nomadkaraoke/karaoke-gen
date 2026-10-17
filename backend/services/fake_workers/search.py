"""Deterministic audio search for the local stack (no flacfetch, no YouTube).

Returns up to three results for any artist/title, in the shapes the web UI
ranks: a lossless torrent, a Spotify match and a YouTube upload. Downloads are
served by ``pipeline.fake_download_audio``, keyed on nothing but the job, so
every provider "works".

``FakeConfig.search_confidence`` shapes them into the UI's confidence tiers
(``getSearchConfidence`` in frontend/lib/audio-search-utils.ts):

- ``perfect`` (tier 1): well-seeded lossless torrent of the right track ("BEST CHOICE").
- ``recommended`` (tier 2): the right track, lossless, but only 20 seeders.
- ``unsure`` (tier 3): every result names a different track; 3 seeders.

``frontend_fixture()`` writes these shapes to ``FRONTEND_FIXTURE``, which the
frontend test ``lib/__tests__/fake-search-tiers.test.ts`` feeds to the real
ranking (a backend test keeps the file current), so the two can't drift.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List

from karaoke_gen.audio_fetcher import AudioSearchResult, NoResultsError

FRONTEND_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "frontend", "lib", "__tests__", "fixtures", "fake-search-tiers.json",
)
# A different song by nobody in particular: what "unsure" results are named.
_MISMATCHED_TITLE = "Completely Different Tune"


def _source_id(provider: str, artist: str, title: str) -> str:
    return hashlib.sha1(f"{provider}:{artist}:{title}".lower().encode()).hexdigest()[:16]


def build_results(artist: str, title: str, duration: int, confidence: str = "perfect") -> List[AudioSearchResult]:
    """The three fake results for ``artist`` / ``title`` in ``confidence``'s shape."""
    seeders = {"perfect": 120, "recommended": 20, "unsure": 3}[confidence]
    track = _MISMATCHED_TITLE if confidence == "unsure" else title
    results = [
        AudioSearchResult(
            title=track, artist=artist, provider="RED",
            url=None, duration=duration, quality="FLAC 16bit CD",
            source_id=_source_id("RED", artist, title), seeders=seeders,
            target_file=f"01 - {track}.flac",
            raw_result={
                "year": 2001, "label": "Fake Records", "release_type": "Album",
                "is_lossless": True, "seeders": seeders, "match_score": 1.0 if track == title else 0.3,
                "quality_str": "FLAC 16bit CD", "formatted_duration": f"{duration // 60}:{duration % 60:02d}",
                "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
            },
        ),
        AudioSearchResult(
            title=track, artist=artist, provider="Spotify",
            url=f"https://open.spotify.com/track/{_source_id('Spotify', artist, title)}",
            duration=duration, quality="OGG 320kbps",
            source_id=_source_id("Spotify", artist, title),
            raw_result={"is_lossless": False, "quality_str": "OGG 320kbps",
                        "match_score": 0.95 if track == title else 0.3},
        ),
        AudioSearchResult(
            title=f"{artist} - {track} (Official Video)", artist=artist, provider="YouTube",
            url=f"https://www.youtube.com/watch?v={_source_id('YouTube', artist, title)[:11]}",
            duration=duration, quality="opus 160kbps",
            source_id=_source_id("YouTube", artist, title)[:11],
            raw_result={"is_lossless": False, "channel": f"{artist}VEVO", "view_count": 1000000},
        ),
    ]
    for i, result in enumerate(results):
        result.index = i
    return results


def frontend_fixture() -> Dict[str, Any]:
    artist, title = "Fake Artist", "Fake Song"
    return {
        "artist": artist,
        "title": title,
        "shapes": {
            c: [r.to_dict() for r in build_results(artist, title, 30, c)]
            for c in ("perfect", "recommended", "unsure")
        },
    }


class FakeAudioSearchService:
    """Drop-in for ``AudioSearchService`` (the methods the API routes call)."""

    last_remote_search_id = None

    def search(self, artist: str, title: str) -> List[AudioSearchResult]:
        from backend.services.fake_workers.config import get_fake_config

        config = get_fake_config()
        candidates = build_results(
            artist, title, int(config.audio_duration_seconds), config.search_confidence,
        )[: config.search_result_count]
        if not candidates:
            raise NoResultsError(f"No results for {artist} - {title} (fake search)")
        return candidates

    async def search_async(self, artist: str, title: str) -> List[AudioSearchResult]:
        import asyncio

        return await asyncio.to_thread(self.search, artist, title)

    def select_best(self, results: List[AudioSearchResult]) -> int:
        return 0


if __name__ == "__main__":  # python -m backend.services.fake_workers.search --write-fixture
    import sys

    if "--write-fixture" in sys.argv:
        os.makedirs(os.path.dirname(FRONTEND_FIXTURE), exist_ok=True)
        with open(FRONTEND_FIXTURE, "w") as f:
            json.dump(frontend_fixture(), f, indent=2)
            f.write("\n")
        print(f"wrote {os.path.normpath(FRONTEND_FIXTURE)}")
