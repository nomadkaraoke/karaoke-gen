"""Deterministic audio search for the local stack (no flacfetch, no YouTube).

Returns up to three results for any artist/title, in the shapes the web UI
ranks: a well-seeded lossless torrent (the "BEST CHOICE"), a Spotify match and a
YouTube upload. Downloads are served by ``pipeline.fake_download_audio``, keyed
on nothing but the job, so every provider "works".
"""
from __future__ import annotations

import hashlib
from typing import List

from karaoke_gen.audio_fetcher import AudioSearchResult, NoResultsError


def _source_id(provider: str, artist: str, title: str) -> str:
    return hashlib.sha1(f"{provider}:{artist}:{title}".lower().encode()).hexdigest()[:16]


class FakeAudioSearchService:
    """Drop-in for ``AudioSearchService`` (the methods the API routes call)."""

    last_remote_search_id = None

    def search(self, artist: str, title: str) -> List[AudioSearchResult]:
        from backend.services.fake_workers.config import get_fake_config

        config = get_fake_config()
        duration = int(config.audio_duration_seconds)
        candidates = [
            AudioSearchResult(
                title=title, artist=artist, provider="RED",
                url=None, duration=duration, quality="FLAC 16bit CD",
                source_id=_source_id("RED", artist, title), seeders=120,
                target_file=f"01 - {title}.flac",
                raw_result={
                    "year": 2001, "label": "Fake Records", "release_type": "Album",
                    "is_lossless": True, "seeders": 120, "match_score": 1.0,
                    "quality_str": "FLAC 16bit CD", "formatted_duration": f"{duration // 60}:{duration % 60:02d}",
                    "quality_data": {"format": "FLAC", "bit_depth": 16, "media": "CD"},
                },
            ),
            AudioSearchResult(
                title=title, artist=artist, provider="Spotify",
                url=f"https://open.spotify.com/track/{_source_id('Spotify', artist, title)}",
                duration=duration, quality="OGG 320kbps",
                source_id=_source_id("Spotify", artist, title),
                raw_result={"is_lossless": False, "quality_str": "OGG 320kbps", "match_score": 0.95},
            ),
            AudioSearchResult(
                title=f"{artist} - {title} (Official Video)", artist=artist, provider="YouTube",
                url=f"https://www.youtube.com/watch?v={_source_id('YouTube', artist, title)[:11]}",
                duration=duration, quality="opus 160kbps",
                source_id=_source_id("YouTube", artist, title)[:11],
                raw_result={"is_lossless": False, "channel": f"{artist}VEVO", "view_count": 1000000},
            ),
        ][: config.search_result_count]
        if not candidates:
            raise NoResultsError(f"No results for {artist} - {title} (fake search)")
        for i, result in enumerate(candidates):
            result.index = i
        return candidates

    async def search_async(self, artist: str, title: str) -> List[AudioSearchResult]:
        import asyncio

        return await asyncio.to_thread(self.search, artist, title)

    def select_best(self, results: List[AudioSearchResult]) -> int:
        return 0
