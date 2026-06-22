"""MusicBrainz lookup service for Bulk Mode album/release lookup.

Provides artist search, album (release-group) listing, canonical-release
selection, and tracklist retrieval with "extra track" detection (live /
remix / bonus / demo / acoustic / instrumental).

MusicBrainz asks for a descriptive User-Agent and ~1 request/second. We add a
small process-wide throttle and a short in-memory TTL cache because album
lookups are interactive and low-volume.

API shapes (captured from the live API, 2026-06):
- GET /ws/2/artist?query=&fmt=json -> {artists: [{id,name,disambiguation,type,score,country}]}
- GET /ws/2/release-group?artist=&type=album&fmt=json
      -> {release-groups: [{id,title,primary-type,secondary-types:[str],first-release-date}]}
- GET /ws/2/release?release-group=&inc=media&fmt=json
      -> {releases: [{id,title,status,date,country,packaging,disambiguation,media:[{track-count}]}]}
- GET /ws/2/release/{id}?inc=recordings&fmt=json
      -> {id,title,status,date,country,media:[{format,track-count,
           tracks:[{position,title,length,recording:{id,title,disambiguation,length}}]}]}
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "NomadKaraoke/1.0 (contact@nomadkaraoke.com)"
_MIN_INTERVAL_S = 1.0  # MusicBrainz politeness: ~1 req/s
_CACHE_TTL_S = 600  # 10 min — album metadata is effectively static
_REQUEST_TIMEOUT_S = 15.0
_MAX_RETRIES = 3  # MusicBrainz 503s / transient transport errors are common from cloud egress
_RETRY_BACKOFF_S = 0.5

# Country preference for canonical-release tie-breaks (worldwide / Europe / US / UK).
_PREFERRED_COUNTRIES = ["XW", "XE", "US", "GB"]

# Locale → ordered country preference for picking which pressing backs a variant.
# Biases the representative release toward the user's likely region; falls back to
# the worldwide default for anything unmapped. Keyed by the language part of the
# locale (region suffix is ignored, e.g. "en-US" → "en").
_LOCALE_COUNTRY_PREFS: Dict[str, List[str]] = {
    "en": ["GB", "US", "AU", "CA", "IE", "NZ"],
    "es": ["ES", "MX", "AR"],
    "fr": ["FR", "CA", "BE"],
    "de": ["DE", "AT", "CH"],
    "it": ["IT"],
    "pt": ["PT", "BR"],
    "nl": ["NL", "BE"],
    "sv": ["SE"],
    "ja": ["JP"],
    "ko": ["KR"],
    "zh": ["CN", "TW", "HK"],
}


def country_prefs_for_locale(locale: Optional[str]) -> List[str]:
    """Map a next-intl locale to an ordered country preference list.

    Returns a copy of the worldwide default (``_PREFERRED_COUNTRIES``) for an
    empty/unknown locale so callers can mutate freely.
    """
    if not locale:
        return list(_PREFERRED_COUNTRIES)
    base = re.split(r"[-_]", locale)[0].lower()
    return list(_LOCALE_COUNTRY_PREFS.get(base, _PREFERRED_COUNTRIES))

# "Extra" track qualifier keywords. Matched only inside (), [], or after " - "
# so we don't misfire on legitimate titles that happen to contain a word.
_EXTRA_KEYWORDS = [
    "live",
    "remix",
    "remixed",
    "bonus",
    "demo",
    "acoustic",
    "instrumental",
    "rehearsal",
    "alternate",
    "alternative",
    "radio edit",
    "single edit",
    "extended",
    "re-recorded",
    "rerecorded",
    "session",
    "outtake",
]
_EXTRA_KEYWORDS_RE = "|".join(re.escape(k) for k in _EXTRA_KEYWORDS)
# A qualifier segment: (...keyword...), [...keyword...], or " - ...keyword..."
_QUALIFIER_RE = re.compile(
    r"(?:[\(\[][^\)\]]*\b(?:" + _EXTRA_KEYWORDS_RE + r")\b[^\)\]]*[\)\]])"
    r"|(?:\s[-–]\s[^\(\[]*\b(?:" + _EXTRA_KEYWORDS_RE + r")\b)",
    re.IGNORECASE,
)


def classify_extra(title: str, disambiguation: str = "") -> tuple[bool, str]:
    """Return (is_extra, reason). Conservative: only flags clear qualifiers."""
    disamb = (disambiguation or "").strip().lower()
    if disamb:
        for kw in _EXTRA_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", disamb):
                return True, disamb
    m = _QUALIFIER_RE.search(title or "")
    if m:
        return True, m.group(0).strip(" -–()[]")
    return False, ""


class MusicBrainzService:
    """Thin async client around the MusicBrainz web service."""

    def __init__(self) -> None:
        self._last_request_at = 0.0
        self._throttle_lock = asyncio.Lock()
        self._cache: Dict[str, tuple[float, Any]] = {}

    # -- HTTP plumbing -----------------------------------------------------

    async def _throttle(self) -> None:
        # Non-blocking: yields to the event loop instead of stalling it.
        async with self._throttle_lock:
            now = time.monotonic()
            wait = self._last_request_at + _MIN_INTERVAL_S - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        cache_key = path + "?" + urlencode(sorted(params.items()))
        cached = self._cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
            return cached[1]
        params = {**params, "fmt": "json"}
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                    resp = await client.get(
                        f"{MB_BASE}/{path}", params=params, headers={"User-Agent": USER_AGENT}
                    )
                    # MusicBrainz returns 503 when rate-limited/throttled — retry those.
                    if resp.status_code == 503 and attempt < _MAX_RETRIES - 1:
                        last_exc = httpx.HTTPStatusError(
                            "503 from MusicBrainz", request=resp.request, response=resp
                        )
                        await asyncio.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                self._cache[cache_key] = (time.monotonic(), data)
                return data
            except (httpx.TransportError, httpx.TimeoutException) as e:
                # Connection/timeout errors — retry with backoff.
                last_exc = e
                logger.warning("MusicBrainz %s attempt %d/%d failed: %r", path, attempt + 1, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF_S * (attempt + 1))
        # Exhausted retries.
        raise last_exc if last_exc else RuntimeError(f"MusicBrainz request failed: {path}")

    # -- Public API --------------------------------------------------------

    async def search_artists(self, name: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Search artists by name."""
        if not name or not name.strip():
            return []
        data = await self._get("artist", {"query": name.strip(), "limit": limit})
        out = []
        for a in data.get("artists", []):
            out.append(
                {
                    "mbid": a.get("id"),
                    "name": a.get("name"),
                    "disambiguation": a.get("disambiguation") or "",
                    "type": a.get("type"),
                    "country": a.get("country"),
                    "score": a.get("score"),
                }
            )
        return out

    async def get_albums(self, artist_mbid: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List an artist's albums (release-groups), studio albums first, then by date."""
        data = await self._get(
            "release-group",
            {"artist": artist_mbid, "type": "album|ep", "limit": limit},
        )
        groups = []
        for rg in data.get("release-groups", []):
            secondary = rg.get("secondary-types") or []
            groups.append(
                {
                    "release_group_mbid": rg.get("id"),
                    "title": rg.get("title"),
                    "primary_type": rg.get("primary-type"),
                    "secondary_types": secondary,
                    "first_release_date": rg.get("first-release-date") or "",
                    "is_studio": len(secondary) == 0,
                }
            )
        # Studio albums first, then earliest release date.
        groups.sort(key=lambda g: (not g["is_studio"], g["first_release_date"] or "9999"))
        return groups

    @staticmethod
    def _total_tracks(release: Dict[str, Any]) -> int:
        return sum((m.get("track-count") or 0) for m in (release.get("media") or []))

    async def _list_editions(self, release_group_mbid: str) -> List[Dict[str, Any]]:
        data = await self._get(
            "release",
            {"release-group": release_group_mbid, "inc": "media", "limit": 100},
        )
        editions = []
        for r in data.get("releases", []):
            editions.append(
                {
                    "release_mbid": r.get("id"),
                    "title": r.get("title"),
                    "status": r.get("status"),
                    "date": r.get("date") or "",
                    "country": r.get("country"),
                    "packaging": r.get("packaging"),
                    "disambiguation": r.get("disambiguation") or "",
                    "track_count": self._total_tracks(r),
                }
            )
        return editions

    @staticmethod
    def pick_canonical_edition(
        editions: List[Dict[str, Any]],
        preferred_countries: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Pick the standard edition: official, modal track count, earliest, preferred country.

        Choosing the most common track count avoids deluxe/remaster editions that
        tack bonus tracks on the end. ``preferred_countries`` overrides the default
        worldwide-first tie-break (used for locale-aware selection).
        """
        if not editions:
            return None
        prefs = preferred_countries or _PREFERRED_COUNTRIES
        official = [e for e in editions if e.get("status") == "Official"] or editions
        with_tracks = [e for e in official if e.get("track_count", 0) > 0] or official
        counts = Counter(e["track_count"] for e in with_tracks if e.get("track_count", 0) > 0)
        if counts:
            standard = counts.most_common(1)[0][0]
            candidates = [e for e in with_tracks if e["track_count"] == standard]
        else:
            candidates = with_tracks

        def country_rank(e: Dict[str, Any]) -> int:
            c = e.get("country")
            return prefs.index(c) if c in prefs else len(prefs)

        candidates.sort(key=lambda e: (e.get("date") or "9999", country_rank(e)))
        return candidates[0]

    @staticmethod
    def build_variants(
        editions: List[Dict[str, Any]],
        preferred_countries: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Collapse a release-group's editions into distinct tracklist *variants*.

        Country pressings are irrelevant for karaoke (we only need the track names),
        so editions are grouped by ``track_count`` — within one release-group, same
        count ≈ same songs. Each variant exposes a representative release (earliest +
        preferred-country), the earliest year, how many pressings collapsed in, and a
        label/delta relative to the "Original" (earliest) variant. Editions without a
        track count are dropped. See
        ``docs/archive/2026-06-22-bulk-edition-collapse-and-community-versions-design.md``.
        """
        if not editions:
            return []
        official = [e for e in editions if e.get("status") == "Official"] or editions
        with_tracks = [e for e in official if (e.get("track_count") or 0) > 0]
        if not with_tracks:
            return []

        groups: Dict[int, List[Dict[str, Any]]] = {}
        for e in with_tracks:
            groups.setdefault(e["track_count"], []).append(e)

        variants: List[Dict[str, Any]] = []
        for track_count, group in groups.items():
            rep = MusicBrainzService.pick_canonical_edition(group, preferred_countries)
            earliest = min((e.get("date") or "9999") for e in group)
            year = earliest[:4] if earliest and earliest != "9999" else ""
            variants.append(
                {
                    "representative_release_mbid": rep["release_mbid"],
                    "track_count": track_count,
                    "year": year,
                    "pressing_count": len(group),
                    "_earliest": earliest,
                }
            )

        # Original = earliest release, then most pressings, then fewest tracks.
        variants.sort(key=lambda v: (v["_earliest"], -v["pressing_count"], v["track_count"]))
        original = variants[0]
        original_tc = original["track_count"]
        for v in variants:
            v["delta_vs_original"] = v["track_count"] - original_tc
            v["label"] = "Original" if v is original else "Reissue"
            del v["_earliest"]

        # Display order: Original first, then by year ascending, then track count.
        variants.sort(key=lambda v: (v["label"] != "Original", v["year"] or "9999", v["track_count"]))
        return variants

    async def get_release_tracklist(self, release_mbid: str) -> Dict[str, Any]:
        """Fetch a release's tracklist with per-track extra detection."""
        data = await self._get(f"release/{release_mbid}", {"inc": "recordings"})
        tracks: List[Dict[str, Any]] = []
        for medium in data.get("media") or []:
            for t in medium.get("tracks") or []:
                rec = t.get("recording") or {}
                title = t.get("title") or rec.get("title") or ""
                disamb = rec.get("disambiguation") or ""
                is_extra, reason = classify_extra(title, disamb)
                length_ms = t.get("length") or rec.get("length")
                tracks.append(
                    {
                        "position": t.get("position"),
                        "title": title,
                        "recording_mbid": rec.get("id"),
                        "disambiguation": disamb,
                        "length_ms": length_ms,
                        "is_extra": is_extra,
                        "extra_reason": reason,
                    }
                )
        return {
            "release_mbid": data.get("id"),
            "title": data.get("title"),
            "date": data.get("date") or "",
            "country": data.get("country"),
            "tracks": tracks,
        }

    async def get_album_tracklist(
        self,
        release_group_mbid: str,
        preferred_countries: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Resolve the canonical edition of an album and return its tracklist, the raw
        editions, and the collapsed tracklist ``variants``. ``preferred_countries``
        biases representative selection (locale-aware)."""
        editions = await self._list_editions(release_group_mbid)
        canonical = self.pick_canonical_edition(editions, preferred_countries)
        variants = self.build_variants(editions, preferred_countries)
        if not canonical:
            return {
                "release_mbid": None,
                "title": None,
                "tracks": [],
                "editions": editions,
                "variants": variants,
                "selected_variant_mbid": None,
            }
        tracklist = await self.get_release_tracklist(canonical["release_mbid"])
        tracklist["editions"] = editions
        tracklist["canonical_release_mbid"] = canonical["release_mbid"]
        tracklist["variants"] = variants
        tracklist["selected_variant_mbid"] = canonical["release_mbid"]
        return tracklist


_service: Optional[MusicBrainzService] = None


def get_musicbrainz_service() -> MusicBrainzService:
    global _service
    if _service is None:
        _service = MusicBrainzService()
    return _service
